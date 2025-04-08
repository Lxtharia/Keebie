
#!/bin/sh

# This script will create udevrule which will automatically give read/write permisison and run the keebie.service as soon as you plug your keyboard
# find the values vendor-id and product-id by using lsusb command
# Note make sure you copy keebie.service to /etc/systemd/system before executing this script as sudo

a=false
for i in "$1" "$2" "$3" ; do
    # debug echo
    echo "$i"
    if [[ "$i" == "" ]]; then
        a=true
    fi
done
if $a ; then
    echo "Not enough arguments, exiting..."
    exit -1
fi

rule_string="$1"
symlink_file=$(basename "$2")
udev_rule_file="$3"

touch /etc/udev/rules.d/"$3"
cd /etc/udev/rules.d/

printf 'SUBSYSTEM=="input", %s MODE="0666", ENV{SYSTEMD_USER_WANTS}="keebie.service"  TAG+="systemd" SYMLINK+="%s"' "$rule_string" "$symlink_file" > "$udev_rule_file"

if [[ "$4" != "" ]]; then
    echo "Creating symlink for current session. Udev should create the symlink automatically on reboot"
    ln -s "$4" "/dev/$symlink_file"
fi

sleep 1s

# No need to restart the system
udevadm control --reload-rules 
