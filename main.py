from plc import PLC, TagType
from program import StartStopTankProgram

program = StartStopTankProgram()
plc = PLC("demo_plc", program)

plc.define_tag("start_button", TagType.DISCRETE_INPUT, False, native_address="%I0.0")
plc.define_tag("stop_button", TagType.DISCRETE_INPUT, False, native_address="%I0.1")
plc.define_tag("oit_start_button", TagType.INTERNAL_BIT, False, native_address="%M3")
plc.define_tag("oit_stop_button", TagType.INTERNAL_BIT, False, native_address="%M4")
plc.define_tag("start_bit", TagType.INTERNAL_BIT, False, native_address="%M1")
plc.define_tag("stop_bit", TagType.INTERNAL_BIT, True, native_address="%M0")
plc.define_tag("green_light", TagType.DISCRETE_OUTPUT, False, native_address="%Q0.0")
plc.define_tag("red_light", TagType.DISCRETE_OUTPUT, False, native_address="%Q0.1")
plc.define_tag("tank_level", TagType.WORD, 0, native_address="%MW0")
plc.define_tag("tank_fill_permitted", TagType.INTERNAL_BIT, True, native_address="%M11")
plc.define_tag("tank_drain_permitted", TagType.INTERNAL_BIT, False, native_address="%M10")

plc.write("start_button", True)   # simulate pressing start
for i in range(5):
    plc.scan()
    print(i, "level:", plc.read("tank_level"), "green:", plc.read("green_light"), "red:", plc.read("red_light"))