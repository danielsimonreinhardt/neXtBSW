# Board-Port des Bau-Test-Profils S32K148 (ARCH-09, ARCH-58).
#
# Seit **ARCH-58** (customer-project.md N5) steht hier **kein Funktionsschalter mehr**. Die Schalter des Baus
# (PLATFORM_SUPPORT_*) und OPENBSW_PLATFORM werden abgeleitet, nicht von Hand gesetzt:
#
#   OPENBSW_PLATFORM         die Familie des Derivats (NX_FAMILY)
#   IO, CAN, TRANSPORT, UDS  aus dem Projekt (use.dio/adc/input_capture/pwm, use.can, diagnostics)
#   ETHERNET                 aus der Bau-Option NX_ETHERNET, nicht aus dem Board
#   WATCHDOG, MPU            aus der Aussage der Familie, ecu/platforms/<familie>/features.yaml
#
# Woher jeder Schalter kommt und was bewusst nicht abgeleitet wird, steht im Kopf von tools/nxconfig/features.py;
# gesetzt werden sie in der Wurzel-CMakeLists.txt (Block "Funktionsschalter aus Projekt und Port").
#
# **Wofuer diese Datei noch da ist.** Sie wird eingebunden, *nachdem* die abgeleiteten Schalter stehen, und darf
# einen davon ueberschreiben - das ist dann die begruendete Ausnahme dieses Boards (ecu/platforms/README.md) und
# gehoert mit Grund in den Changelog. Ein Board ohne diese Datei baut; sie ist nicht mehr Voraussetzung dafuer.
#
# Dieses Board hat heute **keine Ausnahme**: es unterscheidet sich vom Eval Board nur in Konfiguration und Generat
# (Derivat S32K148 LQFP176, 8-MHz-Quarz, kein SBC, zwei passive Transceiver, zwei CAN-Busse).
#
# Der S32K148 hat ENET, gebaut wird trotzdem ohne Ethernet - das war frueher ein PLATFORM_SUPPORT_ETHERNET OFF
# hier und ist seit ARCH-58 der Standard der Bau-Option NX_ETHERNET. Das Preset buildtest-s32k148-ethernet
# (PLAT-56) schaltet sie ein; das Derivat wird dabei auf einen Ethernet-MAC geprueft.
