#!/usr/bin/env python3
#Keebie by Robin Universe & Friends

from evdev import InputDevice, categorize, ecodes
import sys
import signal
import os
import json
import argparse
import time
import subprocess
import shutil



# Utilities

printDebugs = False # Whether we should print debug information

# Hide some output not strictly needed for interactivity
quietMode = False

def dprint(*args, **kwargs): # Print debug info (or don't)
    if printDebugs == True :
        print(*args, **kwargs)

def qprint(*args, **kwargs): # Print less then necessary info (or don't)
    if quietMode == False :
        print(*args, **kwargs)

# Global vars

installDataDir = "/usr/share/keebie/" # Path where default user configuration files and more and are installed
dataDir = os.path.expanduser("~") + "/.config/keebie/" # Path where user configuration files should be stored
dprint(dataDir)

layerDir = dataDir + "layers/" # Cache the full path to the /layers directory
deviceDir = dataDir + "devices/" # Cache the full path to the /devices directory
scriptDir = dataDir + "scripts/" # Cache the full path to the /scripts directory

pidPath = dataDir + "running.pid" # A Path into which we should store the PID of a running looping instance of keebie



# Signal handling

devicesAreGrabbed = False # A bool to track if devices have beed grabbed

savedPid = False # A bool to store if this process has writen to the PID file
paused = False # A bool to store if the process has sent a pause signal to a running keebie loop
havePaused = False # A bool to store if this process has been signaled to pause by another instance

def signal_handler(signal, frame):
    end()

def end(): # Properly close the device file and exit the script
    qprint() # Make sure there is a newline

    if devicesAreGrabbed == True: # If we need to clean up grabbed macroDevices
        ungrabMacroDevices() # Ungrab all devices
        closeDevices() # Cleanly close all devices

    if havePaused == True: # if we have told a running keebie loop to pause
        sendResume() # Tell it to resume

    if savedPid == True: # If we have writen to the PID file
        removePid() # Remove our PID files

    sys.exit(0) # Exit without error



# Key Ledger

class keyLedger():
    """A class for tracking which keys are pressed, as well how how long and how recently."""
    def __init__(self, name="unnamed ledger"):
        self.name = name # Name of the ledger for debug prints
        
        self.state = 3 # An int representing the state of the ledger; 0, 1, 2, 3 : rising, falling, holding, stale
        self.stateChangeStamp = time.time() # The timestamp of the last state change
        self.peaking = False # Are we peaking (adding new keys; rising or holding)
        
        self.history = "" # Current history of recent key peaks
        self.histories = [] # List of flushed histories

        self.newKeys = [] # List of keys newly down
        self.lostKeys = [] # List of keys newly lost
        self.downKeys = [] # List of keys being held down

    def newKeysStr(self):
        """Return a str of concatenated new keys."""
        keysParsed = ""
        
        for keycode in self.newKeys: # For all new keys
            keysParsed += keycode + "+" # Add them to the string along with a "+"
            
        return keysParsed.rstrip("+") # Return the string we built with the trailing "+" stripped

    def lostKeysStr(self):
        """Return a str of concatenated lost keys."""
        keysParsed = ""
        
        for keycode in self.lostKeys: # For all lost keys
            keysParsed += keycode + "+" # Add them to the string along with a "+"

        return keysParsed.rstrip("+") # Return the string we built with the trailing "+" stripped

    def downKeysStr(self):
        """Return a str of concatenated down keys."""
        keysParsed = ""
        
        for keycode in self.downKeys: # For all down keys
            keysParsed += keycode + "+" # Add them to the string along with a "+"

        return keysParsed.rstrip("+") # Return the string we built with the trailing "+" stripped
        
    def stateChange(self, newState, timestamp = None):
        """Change the ledger state and record the timestamp."""
        if not self.state == newState: # If the newState is actually new
            self.state = newState # Change self.state

            if timestamp == None: # If no timestamp was specified
                timestamp = time.time() # Use the current time

            self.stateChangeStamp = timestamp # Record the timestamp
            # dprint(f"{self.name}) new state {newState} at {timestamp}")

    def stateDuration(self, timestamp = None):
        """Return a float of how long our current state has lasted."""
        if timestamp == None: # If no timestamp was specified
            timestamp = time.time() # Use the current time

        return timestamp - self.stateChangeStamp # Return the time since state change

    def addHistoryEntry(self, entry = None, held = None, timestamp = None):
        """Add an entry to our history."""
        if entry == None: # If no entry was specified
            entry = self.downKeysStr() # Use the currently down keys

        if held == None: # If the whether the key was held was not specified
            held = self.stateDuration((timestamp)) > settings["holdThreshold"] # Set held True if the length of last state surpassed holdThreshold setting

        entry += "+HELD" * held # If held is True note that into the entry

        if not self.history == "": # If the current history is not empty
            self.history += "-" # Add a "-" to our history to separate key peaks

        self.history += entry # Add entry to our history

        dprint(f"{self.name}) added {entry} to history")
        # dprint(f"{self.name}) history is \"{self.history}\"")

    def flushHistory(self):
        """Flush our current history into our histories list."""
        dprint(f"{self.name}) flushing {self.history}")

        self.histories += [self.history, ] # Add our history to our histories
        self.history = "" # Clear our history

    def popHistory(self):
        """Pop the nest item out of our histories list and return it, returns a blank string if no history is available."""
        try: # Try to..
            dprint(f"{self.name}) popping {self.histories[0]}")
            return self.histories.pop(0) # Pop and return the first element of our histories list

        except IndexError: # If no history is available
            return "" # Return an empty string

    def update(self, events=()):
        """Update the ledger with an iteratable of key events (or Nones to update timers)."""
        flushedHistory = False # A bool to store if we flushed any histories this update
        
        for event in events: # For each passed event
            self.newKeys = [] # They are no longer new
            self.lostKeys = [] # What once was lost...

            timestamp = None # A float (or None) for the timestamp of the event, will be passed to other methods
            if not event == None: # If the event is not None
                timestamp = event.timestamp() # Set timestamp to the event's timestamp
                
                if event.type == ecodes.EV_KEY: # If the event is a related to a key, as opposed to a mouse movement or something (At least I think thats what this does)
                    event = categorize(event) # Convert our EV_KEY input event into a KeyEvent
                    keycode = event.keycode # Store the event's keycode
                    keystate = event.keystate # Store the event's key state

                    # dprint(timestamp)

                    if type(keycode) == list: # If the keycode is a list of keycodes (it can happen) 
                        keycode = keycode[0] # Select the first one

                    if keystate in (event.key_down, event.key_hold): # If the key is down
                        if not keycode in self.downKeys: # If the key is not known to be down
                            self.newKeys += [keycode, ] # Add the key to our new keys

                    elif keystate == event.key_up: # If the key was released
                        if keycode in self.downKeys: # If the key was in our down keys
                            self.lostKeys += [keycode, ] # Add the key to our lost keys

                        else: # If the key was not known to be down
                            print(f"{self.name}) Untracked key {keycode} released.") # Print a warning

            if not self.newKeys == []: # if we have new keys (rising edge)
                # dprint()
                dprint(f"{self.name}) >{'>' * len(self.downKeys)} " \
                    f"rising with new keys {self.newKeysStr()}")
                
                self.downKeys += self.newKeys # Add our new keys to our down keys
                self.peaking = True # Store that we are peaking

                if settings["multiKeyMode"] == "combination": # If we are in combination mode
                    self.downKeys.sort() # Sort our down keys to negate the order they were added in
                
                self.stateChange(0, timestamp) # Change to state 0

            elif not self.lostKeys == []: # If we lost keys (falling edge)
                # dprint()
                dprint(f"{self.name}) {'<' * len(self.downKeys)}" \
                    f" falling with lost keys {self.lostKeysStr()}")

                if self.peaking == True: # If we were peaking
                    self.addHistoryEntry(timestamp=timestamp) # Add current down keys (peak keys) to our history
                    self.peaking = False # We are no longer peaking
                    
                for keycode in self.lostKeys: # For each lost key
                    self.downKeys.remove(keycode) # Remove it from our down keys
                
                self.stateChange(1, timestamp) # Change to state 1
                
            elif not self.downKeys == []: # If no keys were added or lost, but we still have down keys (holding)
                # dprint(end = f"{self.name}) {'-' * len(self.downKeys)}" \
                #     f" holding with down keys {self.downKeysStr()}" \
                #     f" since {str(self.stateChangeStamp)[7:17]}" \
                #     f" for {str(self.stateDuration(timestamp))[0:10]}" \
                #     f" {'held' * (self.stateDuration((timestamp)) > settings['holdThreshold'])}\r")

                self.stateChange(2, timestamp) # Change to state 2

            else: # If no keys were added or lost but we don't have any down keys (stale)
                # dprint(end = f"{self.name}) stale since {str(self.stateChangeStamp)[7:17]}" \
                #     f" for {str(self.stateDuration(timestamp))[0:10]}\r")

                self.stateChange(3, timestamp) # Change to state 3

                if self.stateDuration(timestamp) > settings["flushTimeout"] and not self.history == "": # If the duration of this stale state has surpassed flushTimeout setting
                    # dprint()
                    self.flushHistory() # Flush our current history
                    flushedHistory = True # Store that we did so

        return flushedHistory # Return whether we flushed any histories



# Macro device

class macroDevice():
    """A class for managing devices."""
    def __init__(self, deviceJson):
        self.name = deviceJson.split(".json")[0] # Name of device for debugging

        jsonData = readJson(deviceJson, deviceDir) # Cache the data held in the device json file
        self.initialLayer = jsonData["initial_layer"] # Layer for the device the start on
        self.eventFile = jsonData["devFile"]    # The input event file that was symlinked by the udev rule
        self.udevMatchKeys = jsonData["udev_match_keys"] # Strings for udev matching

        self.currentLayer = self.initialLayer # Layer this device is currently on
        self.ledger = keyLedger(self.name) # A keyLedger to track input events on his devicet
        self.device = None # will be an InputEvent instance

    def addUdevRule(self, current_event_file = "", priority = 85):
        """Generate a udev rule for this device."""
        filepath = f"{priority}-keebie-{self.name}.rules" # Name of the file for the rule
        rule_string = ""

        for test in self.udevMatchKeys: # For all the udev tests
            rule_string += test + ", " # Add them together with commas
            dprint(rule_string)

        writeJson(self.name + ".json", {"udev_rule": filepath}, deviceDir) # Save the udev rule filepath for removeDevice()

        subprocess.run(["sudo", "sh", installDataDir + "/setup_tools/udevRule.sh", rule_string, self.eventFile, filepath, current_event_file]) # Run the udev setup script with sudo
        
        subprocess.run(["sudo", "udevadm", "test", "/sys/class/input/event3"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) # Force udev to parse the new rule for the device

    def grabDevice(self):
        """Grab the device and set self.device to the grabbed device."""
        qprint("grabbing device " + self.name)
        self.device = InputDevice(self.eventFile) # Set self.device to the device of self.eventFile
        self.device.grab() # Grab the device

        self.setLeds() # Set the leds based on the current layer

    def ungrabDevice(self):
        """Ungrab the device."""
        qprint("ungrabbing device " + self.name)
        self.device.ungrab() # Do the thing that got said twice

    def close(self):
        """Try to close the device file gracefully."""
        qprint("closing device " + self.name)

        self.device.close() # Close the device

    def read(self, process=True):
        """Read all queued events (if any), update the ledger, and process the keycodes (or don't)."""
        flushedHistories = False # A bool to store if we flushed any histories this update
        try: # Try to...
            flushedHistories = self.ledger.update(self.device.read()) # Update our ledger with any available events

        except BlockingIOError: # If no events are available
            flushedHistories = self.ledger.update((None, )) # Update our ledger so things get flushed if need be

        if process == True and flushedHistories == True: # If we are processing the ledger
            self.processLedger() # Process the newly updated ledger

        return flushedHistories # Return whether we flushed any histories

    def setLeds(self):
        """Set device leds bassed on current layer."""
        if "leds" in readJson(self.currentLayer): # If the current layer specifies LEDs
            if 17 in self.device.capabilities().keys(): # Check if the device had LEDs
                leds = self.device.capabilities()[17] # Get a list of LEDs the device has

                onLeds = readJson(self.currentLayer)["leds"] # Get a list of LEDs to turn on
                dprint(f"device {self.name} setting leds {onLeds} on")

                for led in leds: # For all LEDs on the board
                    if led in onLeds: # If the LED is to be set on
                        self.device.set_led(led, 1) # Set it on
                    else:
                        self.device.set_led(led, 0) # Set it off

            else:
                dprint("Device has no LEDs")

        else:
            print(f"Layer {readJson(self.currentLayer)} has no leds property, writing empty")
            writeJson(self.currentLayer, {"leds": []}) # Write an empty list for LEDs into the current layer
            leds = self.device.capabilities()[17] # Get a list of LEDs the device has

            for led in leds: # For all LEDs on the board
                self.device.set_led(led, 0) # Set it off

    def processLedger(self):
        """Process any flushed histories from our ledger."""
        keycode = self.ledger.popHistory() # Pop a history
        while not keycode == "": # As long as the history we have isn't blank
            self.processKeycode(keycode) # Process it
            keycode = self.ledger.popHistory() # And grab the next one (blank if none are available)
        
    def processKeycode(self, keycode):
        """Parse a command in our current layer bound to the passed keycode (ledger history)."""
        dprint(f"{self.name} is processing {keycode} in layer {self.currentLayer}") # Print debug info

        if keycode in readJson(self.currentLayer): # If the keycode is in our current layer's json file
            value = readJson(self.currentLayer)[keycode] # Get the instructions associated with the keycode
            value = parseVars(value, self.currentLayer) # Parse any varables that may appear in the command

            if value.startswith("layer:"): # If value is a layerswitch command
                if os.path.exists(layerDir+value.split(':')[-1] + ".json") == False: # If the layer has no json file
                    createLayer(value.split(':')[-1]+".json") # Create one
                    print("Created layer file: " + value.split(':')[-1]+".json") # Notify the user
                    self.currentLayer = value.split(':')[-1] + ".json" # Switch to our new layer file
                    print("Switched to layer file: " + value.split(':')[-1] + ".json") # Notify the user

                else:
                    self.currentLayer = value.split(':')[-1] + ".json" # Set self.current layer to the target layer
                    print("Switched to layer file: " + value.split(':')[-1] + ".json") # Notify the user

                self.setLeds() # Set LEDs based on the new current layer

            else:
                if value.strip().endswith("&") == False and settings["forceBackground"]: # If value is not set in run in the background and our settings say to force running in the background
                    value += " &" # Force running in the background
                    
                if value.strip().endswith("&") == False and settings["backgroundInversion"]: # If value is not set to run in the background and our settings say to invert background mode
                    value += " &" # Force running in the background
                
                elif value.strip().endswith("&") and settings["backgroundInversion"]: # Else if value is set to run in the background and our settings say to invert background mode
                    value = value.rstrip(" &") # Remove all spaces and &s from the end of value, there might be a better way but this is the best I've got

                scriptTypes = { # A dict of script types and thier interpreters with a trailing space
                    "script": "bash ",
                    "py": "python ",
                    "py2": "python2 ",
                    "py3": "python3 ",
                    "exec": "",
                }

                for scriptType in scriptTypes.keys(): # For recognized script types
                    if value.startswith(scriptType + ":"): # Check if value is one of said script types
                        print(f"Executing {scriptTypes[scriptType]}script {value.split(':')[-1]}") # Notify the user we re running a script
                        value = scriptTypes[scriptType] + scriptDir + value.split(':')[-1] # Set value to executable format
                        break # Break the loop
                
                else: # If this is not a script (i.e. it is a shell command)
                    print(keycode+": "+value) # Notify the user of the command
                
                os.system(value) # Execute value

    def clearLedger(self):
        """Clear this devices ledger."""
        try: # Try to...
            for event in self.device.read(): # For all queued events
                pass # Completely ignore them
        
        except BlockingIOError: # If there arn't any queued events
            pass # Ignore that too
        
        self.ledger = keyLedger(self.name) # Reset the ledger


macroDeviceList = [] # List of macroDevice instances

standardLeds = { # A dict of standard LED ids and thier names
    0: "num lock",
    1: "caps lock",
    2: "scroll lock",
}

def setupMacroDevices():
    """Setup a macroDevice instance based on the contents of deviceDir."""
    global macroDeviceList # Globalize macroDeviceList
    deviceJsonList = [deviceJson for deviceJson in os.listdir(deviceDir) if os.path.splitext(deviceJson)[1] == ".json"] # Get list of json files in deviceDir
    
    dprint(deviceJsonList) # Print debug info
    dprint([device.name for device in macroDeviceList])

    for device in macroDeviceList: # For all preexisting devices
        if not device.name + ".json" in deviceJsonList: # If a preexisting device is not in our list of devices
            dprint(f"Device {device.name} has been removed")
            macroDeviceList.remove(device) # Delete it (It should already be closed)

    dprint([device.name for device in macroDeviceList])

    newMacroDeviceList = [] # Set up an empty list for new devices
    for deviceJson in deviceJsonList: # For all json files in deviceDir
        for device in macroDeviceList: # For all preexisting devices
            if deviceJson == device.name + ".json": # If the new device is already known
                dprint(f"Device {device.name} already known")
                break

        else: # If the loop was never broken
            dprint("New device " + deviceJson)
            newMacroDeviceList += [macroDevice(deviceJson), ] # Set up a macroDevice instance for all files and save them to newMacroDeviceList

    macroDeviceList += newMacroDeviceList # Add the list ofnew devices to the list of preexisting ones

def grabMacroDevices():
    """Grab all devices with macroDevices."""
    global devicesAreGrabbed # Globallize devicesAreGrabbed
    devicesAreGrabbed = True # And set it true

    for device in macroDeviceList:
        device.grabDevice()

def ungrabMacroDevices():
    """Ungrab all devices with macroDevices."""
    global devicesAreGrabbed # Globalize devicesAreGrabbed
    devicesAreGrabbed = False # And set it false

    for device in macroDeviceList:
        device.ungrabDevice()

def closeDevices():
    """Close all devices."""
    for device in macroDeviceList:
        device.close()

def mergeDeviceLedgers():
    """Merge the key ledgers of all macroDevices into one and return it."""
    returnLedger = keyLedger() # Create an empty key ledger
    
    for device in macroDeviceList: # For all macroDevices
        returnLedger.newKeys += device.ledger.newKeys # Add the devices key lists to the return ledger
        returnLedger.lostKeys += device.ledger.lostKeys
        returnLedger.downKeys += device.ledger.downKeys

        returnLedger.histories += device.ledger.histories # Add the devices histories to the return ledger

    return returnLedger # Return the ledger we built

def clearDeviceLedgers():
    """Clear all device ledgers."""
    for device in macroDeviceList:
        device.clearLedger()

def readDevices(process=True):
    """Read and optionally process all devices events."""
    flushedHistories = False # A bool to store if we flushed any histories this update
    for device in macroDeviceList: # For all macroDevices
        if device.read(process) == True: # If any of our devices flush any histories
            flushedHistories = True # Store that

    return flushedHistories # Return whether we flushed any histories

def popDeviceHistories():
    """Pop and return all histories of all devices as a list."""
    histories = [] # A list for poped histories
    for device in macroDeviceList: # For all macroDevices
        keycode = device.ledger.popHistory() # Pop a history
        while not keycode == "": # As long as the history we have isn't blank
            histories += [keycode, ] # Add it to the list
            keycode = device.ledger.popHistory() # And grab the next one (blank if none are available)

    return histories # Return the histories we got



# JSON

def readJson(filename, dir = layerDir): # Reads the file contents of a layer (or any json file named filename in the directory dir)
    with open(dir+filename) as f:
        data = json.load(f)

    return data 

def writeJson(filename, data, dir = layerDir): # Appends new data to a specified layer (or any json file named filename in the directory dir)
    try: # Try to...
        with open(dir+filename) as f: # Open an existing file
            prevData = json.load(f) # And copy store its data
    except FileNotFoundError: # If the file doesn't exist
        prevData = {}

    prevData.update(data)

    with open(dir+filename, 'w+') as outfile:
        json.dump(prevData, outfile, indent=3)

def popDictRecursive(dct, keyList): # Given a dict and list of key names of dicts follow said list into the dicts recursivly and pop the finall result, it's hard to explain 
    if len(keyList) == 1:
        dct.pop(keyList[0])

    elif len(keyList) > 1:
        popDictRecursive(dct[keyList[0]], keyList[1:])

def popJson(filename, key, dir = layerDir): # Removes the key key and it's value from a layer (or any json file named filename in the directory dir)
    with open(dir+filename) as f:
        prevData = json.load(f)

    if type(key) == str:
        prevData.pop(key)
    elif type(key) == list:
        popDictRecursive(prevData, key)

    with open(dir+filename, 'w+') as outfile:
        json.dump(prevData, outfile, indent=3)



# Layer file

def createLayer(filename): # Creates a new layer with a given filename
    shutil.copyfile(installDataDir + "/data/layers/default.json", layerDir + filename) # Copy the provided default layer file from installedDataDir to specified filename



# Settings file

settings = { # A dict of settings to be used across the script
    "multiKeyMode": "combination",
    "forceBackground": False,
    "backgroundInversion": False,
	"loopDelay": 0.0167,
    "holdThreshold": 1,
    "flushTimeout": 0.5,
}

settingsPossible = { # A dict of lists of valid values for each setting (or if first element is type then list of acceptable types in descending priority)
    "multiKeyMode": ["combination", "sequence"],
    "forceBackground": [True, False],
    "backgroundInversion": [True, False],
	"loopDelay": [type, float, int],
    "holdThreshold": [type, float, int],
    "flushTimeout": [type, float, int],
}

def getSettings(): # Reads the json file specified on the third line of config and sets the values of settings based on it's contents
    dprint(f"Loading settings from {dataDir}/settings.json") # Notify the user we are getting settings and tell them the file we are using to do so

    settingsFile = readJson("settings.json", dataDir) # Get a dict of the keys and values in our settings file
    for setting in settings.keys(): # For every setting we expect to be in our settings file
        if type == settingsPossible[setting][0]: # If first element is type
            if type(settingsFile[setting]) in settingsPossible[setting]: # If the value in our settings file is valid
                dprint(f"Found valid typed value: \"{type(settingsFile[setting])}\" for setting: \"{setting}\"")
                settings[setting] = settingsFile[setting] # Write it into our settings
            else :
                print(f"Value: \"{settingsFile[setting]}\" for setting: \"{setting}\" is of invalid type, defaulting to {settings[setting]}") # Warn the user of invalid settings in the settings file
        else:
            if settingsFile[setting] in settingsPossible[setting]: # If the value in our settings file is valid
                dprint(f"Found valid value: \"{settingsFile[setting]}\" for setting: \"{setting}\"")
                settings[setting] = settingsFile[setting] # Write it into our settings
            else :
                print(f"Value: \"{settingsFile[setting]}\" for setting: \"{setting}\" is invalid, defaulting to {settings[setting]}") # Warn the user of invalid settings in the settings file

    dprint(f"Settings are {settings}") # Debug info



# Keypress processing

def parseVars(commandStr, layer): # Given a command from the layer json file replace vars with their values and return the string
    # Vars we will need in the loop
    returnStr = "" # The string to be retuned
    escaped = False # If we previously encountered an escape char
    escapeChar = "\\" # What is out escape char
    varChars = ("%", "%") # What characters start and end a varable name
    inVar = False # If we are in a varable name
    varName = "" # What the varables name is so far

    for char in commandStr : # Iterate over the cars of the input
        if escaped == True : # If char is escaped add it unconditionally and reset escaped
            returnStr += char
            escaped = False
            continue

        if escaped == False and char == escapeChar : # If char is en unescaped escape char set escaped
            escaped = True
            continue

        if inVar == False and char == varChars[0] : # If we arn't in a varable and chars is the start of one set inVar
            inVar = True
            continue

        if inVar == True and char == varChars[1] : # If we are in a varable and char ends it parse the varables value, add it to returnStr if valid, and reset inVar and varName
            try :
                returnStr += readJson(layer)["vars"][varName]
            except KeyError :
                print(f"unknown var {varName} in command {commandStr}, skiping command")
                return ""

            inVar = False
            varName = ""
            continue

        if inVar == True : # If we are in a varable name add char to varName
            varName += char
            continue

        returnStr += char # If none of the above (because we use continue) add char to returnStr

    return returnStr # All done, return the result

def getHistory(): # Return the first key history we get from any of our devices
    clearDeviceLedgers() # Clear all device ledgers
    
    while readDevices(False) == False: # Read events until a history is flushed
        time.sleep(settings["loopDelay"]) # Sleep so we don't eat the poor little CPU
    
    return popDeviceHistories()[0] # Store the first history



# Shells

def getLayers(): # Lists all the json files in /layers and thier contents
    print("Available Layers: \n")
    layerFt = ".json"
    layerFi = {}
    layers = [i for i in os.listdir(layerDir) if os.path.splitext(i)[1] == layerFt] # Get a list of paths to all files that match our file extension

    for f in layers:
        with open(os.path.join(layerDir,f)) as file_object:
            layerFi[f] = file_object.read() # Build a list of the files at those paths
    
    for i in layerFi:
        print(i+layerFi[i]) # And display thier contents to the user
    end()

def detectKeyboard(path = "/dev/input/by-id/"): # Detect what file a keypress is coming from
    print("Gaining sudo to watch root owned files, sudo may prompt you for a password") # Warn the user we need sudo
    subprocess.run(["sudo", "echo",  "have sudo"]) # Get sudo

    print("Please press a key on the desired input device...")
    time.sleep(.5) # Small delay to avoid detecting the device you started the script with
    dev = ""
    while dev == "": # Wait for this command to output the device name, loops every 1s
        dev = subprocess.check_output("sudo inotifywatch " + path +"/* -t 1 2>&1 | grep " + path + " | awk 'NF{ print $NF }'", shell=True ).decode('utf-8').strip()
    return dev

def addKey(layer = "default.json", key = None, command = None, keycodeTimeout = 1): # Shell for adding new macros
    if key == None and command == None:
        relaunch = True
    else:
        relaunch = False
    
    if command == None:
        command = input("Enter the command you would like to attribute to a key on your second keyboard \n") # Get the command the user wishs to bind

        if command.startswith("layer:"): # If the user entered a layer switch command
            if os.path.exists(command.split(':')[-1]+".json") == False: # Check if the layer json file exsits
                createLayer(command.split(':')[-1]+".json") # If not create it
                print("Created layer file: " + command.split(':')[-1]+".json") # And notify the user

                print("standard LEDs:")
                for led in standardLeds.items(): # For all LEDs on most boards
                    print(f"-{led[0]}: {led[1]}") # List it

                onLeds = input("Please choose what LEDs should be enable on this layer (comma and/or space separated list)") # Prompt the user for a list of LED numbers
                onLeds = onLeds.replace(",", " ").split() # Split the input list

                onLedsInt = []
                for led in onLeds: # For all strs in the split list
                    onLedsInt.append(int(led)) # Cast the str to int and add it to a list

                writeJson(command.split(':')[-1]+".json", {"leds": onLedsInt}) # Write the input list to the layer file

    if key == None:
        print(f"Please the execute keystrokes you would like to assign the command to and wait for the next prompt.")
        key = getHistory()

    inp = input(f"Assign {command} to [{key}]? [Y/n] ") # Ask the user if we (and they) got the command and binding right
    if inp == 'Y' or inp == '': # If we did 
        newMacro = {}
        newMacro[key] = command
        writeJson(layer, newMacro) # Write the binding into our layer json file
        print(newMacro) # And print it back

    else: # If we didn't
        print("Addition cancelled.") # Confirm we have cancelled the binding

    if relaunch:
        rep = input("Would you like to add another Macro? [Y/n] ") # Offer the user to add another binding

        if rep == 'Y' or rep == '': # If they say yes
            addKey(layer) # Restart the shell

        end()

def editSettings(): # Shell for editing settings
    settingsFile = readJson("settings.json", dataDir) # Get a dict of the keys and values in our settings file
    
    settingsList = [] # Create a list for key-value pairs of settings 
    for setting in settings.items(): # For every key-value pair in our settings dict
        settingsList += [setting, ] # Add the pair to our list of seting pairs

    print("Choose what value you would like to edit.") # Ask the user to choose which setting they wish to edit
    for settingIndex in range(0, len(settingsList)): # For the index number of every setting pair in our list of setting pairs
        print(f"-{settingIndex + 1}: {settingsList[settingIndex][0]}   [{settingsList[settingIndex][1]}]") # Print an entry for every setting, as well as a number associated with it and it's current value
    
    selection = input("Please make you selection: ") # Take the users input as to which setting they wish to edit
    
    try: # Try to...
        intSelection = int(selection) # Convert the users input from str to int
    
    except ValueError: # If the conversion to int fails
        print("Exiting...") # Tell the user we are exiting
        end() # And do so

    if intSelection in range(1, len(settingsList) + 1): # If the users input corresponds to a listed setting
        settingSelected = settingsList[int(selection) - 1][0] # Store the selected setting's name
        print(f"Editing item \"{settingSelected}\"") # Tell the user we are thier selection
    
    else: # If the users input does not correspond to a listed setting
        print("Input out of range, exiting...") # Tell the user we are exiting
        end() # And do so

    if type == settingsPossible[settingSelected][0]: # If first element of settingsPossible is type
        print(f"Enter a value {settingSelected} that is of one of these types.")
        for valueIndex in range(1, len(settingsPossible[settingSelected])): # For the index number of every valid type of the users selected setting
            print("- " + settingsPossible[settingSelected][valueIndex].__name__) # Print an entry for every valid type
            
        selection = input("Please enter a value: ") # Prompt the user for input

        if selection == "": # If none is provided
            print("Exiting...")
            end() # Exit

        for typePossible in settingsPossible[settingSelected]: # For all valid types
            dprint(typePossible)
            if typePossible == type: # If it is type
                continue
            try: # Try to...
                selection = typePossible(selection) # Cast the users input to the type
                break
            except ValueError: # If casting fails
                pass
        
        if type(selection) in settingsPossible[settingSelected]: # If we have successfully casted to a valid type
            writeJson("settings.json", {settingSelected: selection}, dataDir) # Write the setting into the settings file
            print(f"Set \"{settingSelected}\" to \"{selection}\"")
        else:
            print("Input can't be casted to a supported type, exiting...") # Complain about the bad input
            end() # And exit

    else:
        print(f"Choose one of {settingSelected}\'s possible values.") # Ask the user to choose which value they want to assign to their selected setting
        for valueIndex in range(0, len(settingsPossible[settingSelected])): # For the index number of every valid value of the users selected setting
            print(f"-{valueIndex + 1}: {settingsPossible[settingSelected][valueIndex]}", end = "") # Print an entry for every valid value, as well as a number associate, with no newline
            if settingsPossible[settingSelected][valueIndex] == settings[settingSelected]: # If a value is the current value of the selected setting
                print("   [current]") # Tell the user and add a newline

            else:
                print() # Add a newline

        selection = input("Please make you selection: ") # Take the users input as to which value they want to assign to their selected setting

        try: # Try to...
            intSelection = int(selection) # Convert the users input from str to int
            if intSelection in range(1, len(settingsPossible[settingSelected]) + 1): # If the users input corresponds to a listed value
                valueSelected = settingsPossible[settingSelected][int(selection) - 1] # Store the selected value
                writeJson("settings.json", {settingSelected: valueSelected}, dataDir) # Write it into our settings json file
                print(f"Set \"{settingSelected}\" to \"{valueSelected}\"") # And tell the user we have done so
            
            else: # If the users input does not correspond to a listed value
                print("Input out of range, exiting...") # Tell the user we are exiting
                end() # And do so

        except ValueError: # If the conversion to int fails
            print("Exiting...") # Tell the user we are exiting
            end() # And do so

    getSettings() # Refresh the settings in our settings dict with the newly changed setting

    rep = input("Would you like to change another setting? [Y/n] ") # Offer the user to edit another setting

    if rep == 'Y' or rep == '': # If they say yes
        editSettings() # Restart the shell

    else:
        end()

def editLayer(layer = "default.json"): # Shell for editing a layer file (default by default)
    LayerDict = readJson(layer, layerDir) # Get a dict of keybindings in the layer file
    
    keybindingsList = [] # Create a list for key-value pairs of keybindings
    for keybinding in LayerDict.items(): # For every key-value pair in our layers dict
        keybindingsList += [keybinding, ] # Add the pair to our list of keybinding pairs

    print("Choose what binding you would like to edit.") # Ask the user to choose which keybinding they wish to edit
    for bindingIndex in range(0, len(keybindingsList)): # For the index number of every binding pair in our list of binding pairs
        if keybindingsList[bindingIndex][0] == "leds":
            print(f"-{bindingIndex + 1}: Edit LEDs")
        elif keybindingsList[bindingIndex][0] == "vars":
            print(f"-{bindingIndex + 1}: Edit layer varables")
        else:
            print(f"-{bindingIndex + 1}: {keybindingsList[bindingIndex][0]}   [{keybindingsList[bindingIndex][1]}]") # Print an entry for every binding, as well as a number associated with it and it's current value
    
    selection = input("Please make you selection: ") # Take the users input as to which binding they wish to edit
    
    try: # Try to...
        intSelection = int(selection) # Comvert the users input from str to int
        if intSelection in range(1, len(keybindingsList) + 1): # If the users input corresponds to a listed binding
            bindingSelected = keybindingsList[int(selection) - 1][0] # Store the selected bindings's key
            print(f"Editing item \"{bindingSelected}\"") # Tell the user we are editing their selection
        
        else: # If the users input does not correspond to a listed binding
            print("Input out of range, exiting...") # Tell the user we are exiting
            end() # And do so

    except ValueError: # If the conversion to int fails
        print("Exiting...") # Tell the user we are exiting
        end() # And do so

    if bindingSelected == "leds":
        print("standard LEDs:")
        for led in standardLeds.items(): # For all LEDs on most boards
            print(f"-{led[0]}: {led[1]}") # List it

        onLeds = input("Please choose what LEDs should be enable on this layer (comma and/or space separated list)") # Prompt the user for a list of LED numbers
        onLeds = onLeds.replace(",", " ").split() # Split the input list

        onLedsInt = []
        for led in onLeds: # For all strs in the split list
            onLedsInt.append(int(led)) # Cast the str to int and add it to a list

        writeJson(layer, {"leds": onLedsInt}) # Write the input list to the layer file

    elif bindingSelected == "vars":
        varsDict = readJson(layer, layerDir)["vars"] # Get a dict of layer vars in the layer file
        
        varsList = [] # Create a list for key-value pairs of layer vars
        for var in varsDict.items(): # For every key-value pair in our layer vars dict
            varsList += [var, ] # Add the pair to our list of layer var pairs

        print("Choose what varable you would like to edit.") # Ask the user to choose which var they wish to edit
        for varIndex in range(0, len(varsList)): # For the index number of every var pair in our list of var pairs
            print(f"-{varIndex + 1}: {varsList[varIndex][0]}   [{varsList[varIndex][1]}]")
            
        selection = input("Please make you selection: ") # Take the users input as to which var they wish to edit
    
        try: # Try to...
            intSelection = int(selection) # Comvert the users input from str to int
            if intSelection in range(1, len(varsList) + 1): # If the users input corresponds to a listed var
                varSelected = varsList[int(selection) - 1][0] # Store the selected var's key
                print(f"Editing item \"{varSelected}\"") # Tell the user we are editing their selection
            
            else: # If the users input does not correspond to a listed var
                print("Input out of range, exiting...") # Tell the user we are exiting
                end() # And do so

        except ValueError: # If the conversion to int fails
            print("Exiting...") # Tell the user we are exiting
            end() # And do so

        print(f"Choose am action to take on {varSelected}.") # Ask the user to choose what they want to do with their selected var
        # Prompt the user with a few possible actions
        print("-1: Delete varable.")
        print("-2: Edit varable name.")
        print("-3: Edit varable value.")
        print("-4: Cancel.")

        selection = input("Please make you selection: ") # Take the users input as to what they want to do with their selected var

        try: # Try to...
            intSelection = int(selection) # Convert the users input from str to int

            if intSelection == 1: # If the user selected delete
                popJson(layer, ["vars", varSelected]) # Remove the var
            elif intSelection == 2: # If the user selected edit name
                varName = input("Please input new name: ") # Ask the user for a new name
                varsDict.update({varName: varsDict[varSelected]}) # Add new name and value to varDict
                writeJson(layer, {"vars": varsDict}) # Set layer's vars to varDict
                popJson(layer, ["vars", varSelected]) # Note: if the user replaces the original name with the same name this will delete the binding
            elif intSelection == 3: # If the user selected edit value
                varVal = input("Please input new value: ") # Ask the user for a new value
                varsDict.update({varSelected: varVal}) # Update name to new value in varDict
                writeJson(layer, {"vars": varsDict}) # Set layer's vars to varDict
            elif intSelection == 4: # If the user selected cancel
                pass # Pass back to the previous level

            else: # If the users input does not correspond to a listed value
                print("Input out of range, exiting...") # Tell the user we are exiting
                end() # And do so

        except ValueError: # If the conversion to int fails
            print("Exiting...") # Tell the user we are exiting
            end() # And do so

    else:
        print(f"Choose am action to take on {bindingSelected}.") # Ask the user to choose what they want to do with their selected binding
        # Prompt the user with a few possible actions
        print("-1: Delete binding.")
        print("-2: Edit binding key.")
        print("-3: Edit binding command.")
        print("-4: Cancel.")

        selection = input("Please make you selection: ") # Take the users input as to what they want to do with their selected binding

        try: # Try to...
            intSelection = int(selection) # Convert the users input from str to int

            if intSelection == 1: # If the user selected delete
                popJson(layer, bindingSelected) # Remove the binding
            elif intSelection == 2: # If the user selected edit key
                addKey(layer, command = LayerDict[bindingSelected]) # Launch the key addition shell and preserve the command
                popJson(layer, bindingSelected) # Note: if the user replaces the original key with the same key this will delete the binding
            elif intSelection == 3: # If the user selected edit command
                addKey(layer, key = bindingSelected) # Launch the key addition shell and preserve the key
            elif intSelection == 4: # If the user selected cancel
                pass # Pass back to the previous level

            else: # If the users input does not correspond to a listed value
                print("Input out of range, exiting...") # Tell the user we are exiting
                end() # And do so

        except ValueError: # If the conversion to int fails
            print("Exiting...") # Tell the user we are exiting
            end() # And do so

    rep = input("Would you like to edit another binding? [Y/n] ") # Offer the user to edit another binding

    if rep == 'Y' or rep == '': # If they say yes
        editLayer(layer) # Restart the shell

    else:
        end()

def newDevice(eventPath = "/dev/input/"):
    """Add a new json file to devices/."""
    print("Setting up device")

    deviceName = input("Please provide a name for this device.\nThis name will be used to create a symlink to the device in /dev/<devicename> so choose something unique.\nName: ").strip()
    while deviceName == "":
        deviceName = input("Name cannot be empty. Name: ").strip()

    initialLayer = input("Please provide a name for for this devices initial layer (non-existent layers will be created, default.json by default): ") # Prompt the user for a layer filename

    if initialLayer.strip() == "": # If the user did not provide a layer name
        initialLayer = "default.json" # Default to default.json

    if os.path.exists(layerDir + initialLayer) == False: # If the users chosen layer does not exist
        createLayer(initialLayer) # Create it

    eventFile = detectKeyboard(eventPath) # Prompt the user for a device
    # eventFile = os.path.basename(eventFile) # Get the devices filename from its filepath

    input("\nA udev rule will be made next, sudo may prompt you for a password. Press enter to continue...") # Ensure the stdin is empty

    dev = InputDevice(eventFile)

    selectedPropertiesList = [f'ATTRS{{phys}}=="{dev.phys}"'] # Make an udev rule matching the device file
    symlink_name = "/dev/" + deviceName

    # create udevrule

    deviceJsonDict = { # Construct the device data dict
        "initial_layer": initialLayer,
        "devFile": symlink_name,
        "udev_match_keys": selectedPropertiesList
    }

    writeJson(deviceName + ".json", deviceJsonDict, deviceDir) # Write device data into a json file

    macroDevice(deviceName + ".json").addUdevRule(eventFile) # Create a mcro device and make a udev rule, the user will be prompted for sudo

    end()

def removeDevice(name = None):
    """Removes a device file from deviceDir and udev rule based on passed name. If no name is passed prompt the user to choose one."""
    if name == None or name == True: # If no name was provided
        print("Devices:")

        deviceList = os.listdir(deviceDir) # Get a list of device files
        for deviceIndex in range(0, len(deviceList)): # For all device files
            print(f"-{deviceIndex + 1}: {deviceList[deviceIndex]}") # Print thier names
        
        selection = int(input("Please make your selection : ")) # Prompt the user for a selection
        name = deviceList[selection - 1] # Set name based on the users selection
    
    udevRule = readJson(name, deviceDir)["udev_rule"] # Cache the path to the devices udev rule

    print("removing device file and udev rule, sudo may prompt you for a password.") # Warn the user we need sudo
    os.remove(deviceDir + name) # Remove the device file
    subprocess.run(["sudo", "rm", "-f", "/etc/udev/rules.d/" + udevRule]) # Remove the udev rule

    end()



# Setup

def firstUses(): # Setup to be run when a user first runs keebie
    shutil.copytree(installDataDir + "data/", dataDir, dirs_exist_ok=True) # Copy template configuration files to user
    print(f"Configuration files copied from {installDataDir}/data/ to {dataDir}") # And inform the user



# Inter-process communication

def savePid():
    """Save our PID into the PID file. Raise FileExistsError if the PID file already exists."""
    dprint("Saving PID to " + pidPath)

    global savedPid # Globalize savedPid

    pid = os.getpid() # Get this process' PID

    if os.path.exists(pidPath) == False: # If no PID file already exists
        with open(pidPath, "wt") as pidFile: # Create and open the PID file
            pidFile.write(str(pid)) # Write our PID into it
            savedPid = True # Record that we have saved our PID

    else:
        dprint("PID already recorded")
        raise FileExistsError("PID already recorded")

def removePid():
    """Remove the PID file if it exists."""
    dprint("Removing PID file " + pidPath)

    global savedPid # Globalize savedPid

    if os.path.exists(pidPath) == True: # If the PID file exists
        os.remove(pidPath) # Remove it
        savedPid = False # And record it's removal

    else:
        print("PID was never stored?")

def getPid():
    """Return the PID in the PID file. Raise FileNotFoundError if the file does not exist."""
    if os.path.exists(pidPath) == True: # If the PID file exists
        with open(pidPath, "rt") as pidFile: # Open it
            return int(pidFile.read()) # And return it's contents as an int

    else:
        dprint("PID file dosn't exist")
        raise FileNotFoundError("PID file dosn't exist")

def checkPid():
    """Try to get the PID and check if it is valid. Raise FileNotFoundError if the PID file does not exist. Raise ProcessLookupError and remove the PID file if no process has the PID."""
    pid = getPid() # Try to get the PID in the PID file, this will raise en exception if the file is missing

    try:
        os.kill(pid, 0) # Send signal 0 to the process, this will raise OSError if the process doesn't exist
    
    except OSError:
        dprint("PID invalid")
        removePid() # Remove the PID file since its wrong
        raise ProcessLookupError("PID invalid")

def sendStop():
    """If a valid PID is found in the PID file send SIGINT to the process."""
    try:
        dprint("Sending stop")

        checkPid() # Check if the PID file point's to a valid process
        
        os.kill(getPid(), signal.SIGINT) # Stop the process

    except (FileNotFoundError, ProcessLookupError): # If the PID file doesn't exist or the process isn't valid
        dprint("No process to stop")

def sendPause(waitSafeTime=None):
    """If a valid PID is found in the PID file send SIGUSR1 to the process."""
    try:
        dprint("Sending pause")

        checkPid() # Check if the PID file point's to a valid process

        global havePaused
        havePaused = True # Save that we have paused the process
        
        os.kill(getPid(), signal.SIGUSR1) # Pause the process

        if waitSafeTime == None:
            waitSafeTime = settings["loopDelay"] * 3 # Set how long we should wait

        time.sleep(waitSafeTime) # Wait a bit to make sure the process paused itself

    except (FileNotFoundError, ProcessLookupError): # If the PID file doesn't exist or the process isn't valid
        dprint("No process to pause")

def sendResume():
    """If a valid PID is found in the PID file send SIGUSR2 to the process."""
    try:
        dprint("Sending resume")
        
        checkPid() # Check if the PID file point's to a valid process

        global havePaused
        havePaused = False # Save that we have resumed the process

        os.kill(getPid(), signal.SIGUSR2) # Resume the process

    except (FileNotFoundError, ProcessLookupError): # If the PID file doesn't exist or the process isn't 
        dprint("No process to resume")

def pause(signal, frame):
    """Ungrab all macro devices."""
    print("Pausing...")

    global paused
    paused = True # Save that we have been paused)

    ungrabMacroDevices() # Ungrab all devices so the pausing process can use them
    closeDevices() # Close our macro devices

def resume(signal, frame):
    """Grab all macro devices and refresh our setting after being paused (or just if some changes were made we need to load)."""
    print("Resuming...")

    global paused
    
    getSettings() # Refresh our settings

    if paused == True: # If we were paused prior
        setupMacroDevices() # Set our macro devices up again to detect changes
        grabMacroDevices() # Grab all our devices back

    paused = False # Save that we are no longer paused



# Arguments

parser = argparse.ArgumentParser() # Set up command line arguments

parser.add_argument("--layers", "-l", help="Show saved layer files", action="store_true")
parser.add_argument("--detect", "-d", help="Detect keyboard device file", action="store_true")
parser.add_argument("--print-keys", "-k", help="Print a series of keystrokes", action="store_true")

try:
    parser.add_argument("--add", "-a", help="Adds new macros to the selected layer file (or default layer if unspecified)", nargs="?", default=False, const="default.json", metavar="layer", choices=[i for i in os.listdir(layerDir) if os.path.splitext(i)[1] == ".json"])
except FileNotFoundError :
    parser.add_argument("--add", "-a", help="Adds new macros to the selected layer file (or default layer if unspecified)", nargs="?", default=False, const="default.json", metavar="layer")

parser.add_argument("--settings", "-s", help="Edits settings file", action="store_true")

try:
    parser.add_argument("--edit", "-e", help="Edits specified layer file (or default layer if unspecified)", nargs="?", default=False, const="default.json", metavar="layer", choices=[i for i in os.listdir(layerDir) if os.path.splitext(i)[1] == ".json"])
except FileNotFoundError :
    parser.add_argument("--edit", "-e", help="Edits specified layer file (or default layer if unspecified)", nargs="?", default=False, const="default.json", metavar="layer")

parser.add_argument("--new", "-n", help="Add a new device file", action="store_true")

try:
    parser.add_argument("--remove", "-r", help="Remove specified device, if no device is specified you will be prompted", nargs="?", default=False, const=True, metavar="device", choices=[i for i in os.listdir(deviceDir) if os.path.splitext(i)[1] == ".json"])
except FileNotFoundError :
    parser.add_argument("--remove", "-r", help="Remove specified device, if no device is specified you will be prompted", nargs="?", default=False, const=True, metavar="device")
    
parser.add_argument("--pause", "-P", help="Pause a running keebie instance that is processing macros", action="store_true")

parser.add_argument("--resume", "-R", help="Resume a keebie instance paused by --pause", action="store_true")

parser.add_argument("--stop", "-S", help="Stop a running keebie instance that is processing macros", action="store_true")

parser.add_argument("--install", "-I", help="Install default files to your home's .config/ directory", action="store_true")

parser.add_argument("--verbose", "-v", help="Print extra debugging information", action="store_true")

parser.add_argument("--quiet", "-q", help="Print less", action="store_true")

args = parser.parse_args()

printDebugs = args.verbose
quietMode = args.quiet or args.print_keys



# Main code

if not args.print_keys:
    print("Welcome to Keebie")

signal.signal(signal.SIGINT, signal_handler)

if not os.path.exists(dataDir): # If the user we are running as does not have user configuration files
    print("You are running keebie without user configuration files installed") # Inform the user
    firstUses() # Run first time user setup

setupMacroDevices() # Setup all devices

getSettings() # Get settings from the json file in config


if args.layers: # If the user passed --layers
    getLayers() # Show the user all layer json files and their contents

elif args.print_keys:
    sendPause() # Ask a running keebie loop (if one exists) to pause so we can use the devices
    grabMacroDevices()
    print(getHistory()) # Print the first key history we get from any of our devices
    end()

elif args.add: # If the user passed --add
    sendPause() # Ask a running keebie loop (if one exists) to pause so we can use the devices

    grabMacroDevices()
    addKey(args.add) # Launch the key addition shell

elif args.settings: # If the user passed --settings
    sendPause() # Ask a running keebie loop (if one exists) to pause so it will reload its settings when we're done

    editSettings() # Launch the setting editing shell

elif args.detect: # If the user passed --detect
    print(detectKeyboard("/dev/input/")) # Launch the keyboard detection function

elif args.edit: # If the user passed --edit
    sendPause() # Ask a running keebie loop (if one exists) to pause so we can use the devices

    grabMacroDevices()
    editLayer(args.edit) # Launch the layer editing shell

elif args.new: # If the user passed --new
    sendPause() # Ask a running keebie loop (if one exists) to pause so it will detect the new device when we're done

    newDevice() # Launch the device addition shell

elif args.remove: # If the user passed --remove
    sendPause() # Ask a running keebie loop (if one exists) to pause so it will detect the removed device when we're done

    removeDevice(args.remove) # Launch the device removal shell

elif args.pause: # If the user passed --pause
    sendPause(0) # Ask a running keebie loop (if one exists) to pause
    havePaused = False

elif args.resume: # If the user passed --resume
    sendResume() # Ask a running keebie loop (if one exists) to resume

elif args.stop: # If the user passed --stop
    sendStop() # Ask a running keebie loop (if one exists) to run end()

elif args.install: # If the user passed --install
    firstUses() # Perform first time setup

else: # If the user passed nothing
    try:
        savePid() # Try to save our PID to the PID file

    except FileExistsError: # If the PID file already exists
        try:
            checkPid() # Check if it is valid, this will raise an error if it isn't
            print("Another instance of keebie is already processing macros, exiting...") 
            end()

        except ProcessLookupError: # If the PID file pointed to an invalid PID
            savePid() # Save our PID to the PID file (which checkPid() will have removed)

    signal.signal(signal.SIGUSR1, pause) # Bind SIGUSR1 to pause()
    signal.signal(signal.SIGUSR2, resume) # Bind SIGUSR2 to remove()

    time.sleep(.5)
    grabMacroDevices() # Grab all the devices

    while True : # Enter an infinite loop
        if paused == False: # If we are not paused
            readDevices() # Read all devices and process the keycodes
    
        time.sleep(settings["loopDelay"]) # Sleep so we don't eat the poor little CPU
