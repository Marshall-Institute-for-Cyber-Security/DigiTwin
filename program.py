from plc import PLC

class StartStopTankProgram:
    def __init__(self):
        self._prev_fill_condition = False
        self._prev_drain_condition = False

    def __call__(self, plc: PLC):
        start_pb = plc.read_input("start_button")
        stop_pb = plc.read_input("stop_button")
        oit_start = plc.read("oit_start_button")
        oit_stop = plc.read("oit_stop_button")

        # seal-in start/stop bits
        if start_pb or oit_start:
            plc.write("start_bit", True)
            plc.write("stop_bit", False)
        if stop_pb or oit_stop:
            plc.write("stop_bit", True)
            plc.write("start_bit", False)

        start_bit = plc.read("start_bit")
        stop_bit = plc.read("stop_bit")

        plc.write_output("green_light", stop_bit)
        plc.write_output("red_light", start_bit)

        # tank level counter (0-99), fills while running, drains while stopped
        level = plc.read("tank_level")
        fill_permitted = level <= 99
        drain_permitted = level >= 1
        plc.write("tank_fill_permitted", fill_permitted)
        plc.write("tank_drain_permitted", drain_permitted)

        fill_condition = start_bit and fill_permitted
        drain_condition = stop_bit and drain_permitted

        fill_edge = fill_condition and not self._prev_fill_condition
        drain_edge = drain_condition and not self._prev_drain_condition

        if fill_edge:
            level += 1
        if drain_edge:
            level -= 1
        plc.write("tank_level", max(0, min(99, level)))

        self._prev_fill_condition = fill_condition
        self._prev_drain_condition = drain_condition