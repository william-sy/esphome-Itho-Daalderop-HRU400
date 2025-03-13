# esphome-HRU400

This repo will help you manage your HRU400 WTW trough modbus.

## Needed items

### ESP Home device
Current supported boards:
1. m5 stack atom lite
2. m5 stack atom s3 lite

These 2 boards plugin to the m5 stack RS485 base. 
This way you do not need to solder. 

### Other
1. A cable with 3 wires
2. A 3 pin connector that will plug into the WTW unit
example [Removable Screw Terminal - 3p - 3.81mm](https://www.tinytronics.nl/en/cables-and-connectors/connectors/screw-terminals/removable-screw-terminal-3p-3.81mm)

## Boards and UART:
For m5stack boards and UART to RS485 converters: 
Most of the M5stack ESP board have more than 1 way to connect UART devices. With this comes different settings. 

To accommodate this the M5stack boards are being migrated to a different folder (your old one will continue to work).
Here is a layout of where to find what:

```
boards:
    old_boards.yaml
    uart:
        BRAND_NAME:
            UART_SKU_NUMBER:
                README.md
                compatibledevice.yaml
```
So for a s3-lite with a external UART to RS485 from M5stack you would need to configure this in the `hru400.yal`
```YAML
packages:
  remote_package:
    url: https://github.com/william-sy/esphome-Itho-Daalderop-HRU400
    ref: main
    refresh: 0s
    files: [
      esphome/boards/uart/m5stack/U034/m5stack-C124-atom-s3-lite.yaml,
```





## Official documentation
It appears that the official installation documentation has changed on the official website.
Altough there are lots of changes in this new document, it seems to have no impact on the modbus side of things.