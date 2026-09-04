"""Phase 4.5 PoC harness: replace the lab's real Schneider M221 with DigiTwin.

Wires :class:`~digitwin.programs.m221_tank_twin.M221TankTwinProgram` (a 1:1
translation of the real ladder — see that module's docstring) onto the real
:class:`~digitwin.models.PLC_Schneider_TM221CE16T` profile, so tags sit at the
same native addresses the lab's HMI already expects.

``main()`` runs a stand-alone offline smoke test — driving the HMI-side
inputs (``oit_start_button`` / ``oit_stop_button``) the way ``press()`` does
below, over an in-process transport with nothing wired to the physical
%I0.0/%I0.1 terminals, since the physical pushbuttons are out of scope for
this test. ``serve()`` is the real thing: it opens a
:class:`~digitwin.adapters.modbus.ModbusSlaveServer` on the network for the
real HMI to poll, in place of the M221.

``LAB_PUBLISH`` / ``LAB_ACCEPT`` below cover the only four addresses the
Maple Systems HMI project (``used_addresses_list.xls``, sheet "02_Schneider
MODBUS TCP_IP") actually touches on this PLC: %M1 (a status lamp), %M3/%M4
(the two OIT pushbuttons), and %MW0 (bargraph + numeric display). Everything
else this program uses (%M0, %M10, %M11, %M5, %Q0.0/%Q0.1, %I0.0/%I0.1) stays
off the wire — the HMI never touches them.

Register numbers assume the M221's well-known 1:1 native-address mapping
(coil N = %MN, holding register N = %MWN) — the same mapping implied by the
Maple driver taking the native tag ("%M-1", "%MW-0") directly rather than a
raw Modbus address. Not yet confirmed against a packet capture or the M221's
own Modbus mapping table — worth a quick check before a real cutover.

%M1 ("start_bit") and %MW0 ("tank_level") are program-owned values the HMI
only ever reads, but %M is a coil and %MW a holding register on real M221
hardware regardless of who logically "owns" writing them — see
:class:`~digitwin.adapters.modbus.ModbusSlaveServer`'s docstring for why they
go in ``publish`` (not ``accept``) even though coil/holding_register are
nominally the master-writable kinds.
"""

from __future__ import annotations

from digitwin.adapters.modbus import ModbusSlaveServer, RegisterMap
from digitwin.executive import Executive, ExecutiveMode
from digitwin.io import InProcessTransport, IOBus
from digitwin.models import PLC_Schneider_TM221CE16T
from digitwin.plant import NullPlant
from digitwin.plc import PLC, TagType, TagValue
from digitwin.programs import M221TankTwinProgram

# name, type, initial value, native address -- matches SE_Complete.smbp exactly
TagSpec = tuple[str, TagType, TagValue, str]

LAB_TAGS: list[TagSpec] = [
    ("start_button", TagType.DISCRETE_INPUT, False, "%I0.0"),
    ("stop_button", TagType.DISCRETE_INPUT, False, "%I0.1"),
    ("oit_start_button", TagType.INTERNAL_BIT, False, "%M3"),
    ("oit_stop_button", TagType.INTERNAL_BIT, False, "%M4"),
    ("tank_reset", TagType.INTERNAL_BIT, False, "%M5"),  # purpose unverified, see m221_tank_twin.py
    ("start_bit", TagType.INTERNAL_BIT, False, "%M1"),
    ("stop_bit", TagType.INTERNAL_BIT, True, "%M0"),
    ("tank_drain", TagType.INTERNAL_BIT, False, "%M10"),
    ("tank_fill", TagType.INTERNAL_BIT, True, "%M11"),
    ("green_light", TagType.DISCRETE_OUTPUT, False, "%Q0.0"),
    ("red_light", TagType.DISCRETE_OUTPUT, False, "%Q0.1"),
    ("tank_level", TagType.WORD, 0, "%MW0"),
]


# Tag name -> Modbus slot, at the register numbers the HMI already polls
# (see the module docstring for the addressing assumption and its caveat).
LAB_PUBLISH: dict[str, RegisterMap] = {
    "start_bit": RegisterMap(kind="coil", address=1),
    "tank_level": RegisterMap(kind="holding_register", address=0),
}
LAB_ACCEPT: dict[str, RegisterMap] = {
    "oit_start_button": RegisterMap(kind="coil", address=3),
    "oit_stop_button": RegisterMap(kind="coil", address=4),
}


def build_lab_twin_plc(scan_dt: float) -> PLC:
    plc = PLC_Schneider_TM221CE16T("m221_lab_twin", M221TankTwinProgram(scan_dt), watchdog_s=0.05)
    for name, tag_type, initial_value, native_address in LAB_TAGS:
        plc.define_tag(name, tag_type, initial_value, native_address=native_address)
    return plc


def build_lab_twin_modbus_slave(
    plc: PLC, *, host: str = "0.0.0.0", port: int = 502
) -> ModbusSlaveServer:
    return ModbusSlaveServer(plc=plc, publish=LAB_PUBLISH, accept=LAB_ACCEPT, host=host, port=port)


def build_lab_twin(
    mode: ExecutiveMode = ExecutiveMode.FREE_RUN,
    scale: float = 1.0,
    *,
    dt: float = 0.1,
) -> Executive:
    """Assemble the twin PLC with no plant and no field wiring — see the
    module docstring for what replaces the transport once the HMI's register
    map is available."""
    plc = build_lab_twin_plc(dt)
    bus = IOBus()
    transport = InProcessTransport(bus, plc)  # nothing wired: buttons are out of scope
    return Executive(plc, NullPlant(), bus, transport, dt=dt, mode=mode, scale=scale)


def press(sim: Executive, button: str, pressed: bool) -> None:
    """Drive an OIT (HMI) pushbutton tag directly, standing in for the HMI."""
    sim.plc.write(button, pressed)


def _report(sim: Executive) -> None:
    plc = sim.plc
    print(
        f"t={sim.elapsed:4.1f}s  level={int(plc.read('tank_level')):3d}  "
        f"green={int(plc.read('green_light'))} red={int(plc.read('red_light'))}  "
        f"fill_permitted={int(plc.read('tank_fill'))} drain_permitted={int(plc.read('tank_drain'))}"
    )


def main() -> None:
    sim = build_lab_twin()

    press(sim, "oit_start_button", True)
    sim.tick()
    press(sim, "oit_start_button", False)  # release; seal-in holds it running

    for step in range(1, 1101):
        sim.tick()
        if step % 100 == 0:
            _report(sim)
        if step == 700:
            press(sim, "oit_stop_button", True)
            sim.tick()
            press(sim, "oit_stop_button", False)

    print(f"scans={sim.plc.scan_count}  watchdog_tripped={sim.plc.watchdog_tripped}")


def serve(*, host: str = "0.0.0.0", port: int = 502, dt: float = 0.1) -> None:
    """Replace the M221 for real: open a Modbus TCP slave and scan forever
    at real-time cadence, until interrupted. Point the HMI at this host/port
    instead of 192.168.200.200 to run the PoC."""
    plc = build_lab_twin_plc(dt)
    bus = IOBus()
    transport = InProcessTransport(bus, plc)  # nothing wired: buttons are out of scope
    modbus_slave = build_lab_twin_modbus_slave(plc, host=host, port=port)
    sim = Executive(
        plc,
        NullPlant(),
        bus,
        transport,
        dt=dt,
        mode=ExecutiveMode.REAL_TIME,
        modbus_slave=modbus_slave,
    )
    modbus_slave.start()
    print(f"serving Modbus TCP on {host}:{port} -- Ctrl+C to stop")
    try:
        while True:
            sim.tick()
    except KeyboardInterrupt:
        pass
    finally:
        modbus_slave.stop()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode", nargs="?", default="offline", choices=["offline", "serve"],
        help="'offline' runs the smoke test (default); 'serve' opens the Modbus TCP slave",
    )
    parser.add_argument("--host", default="0.0.0.0", help="serve: interface to bind (default all)")
    parser.add_argument("--port", type=int, default=502, help="serve: TCP port (default 502)")
    args = parser.parse_args()

    if args.mode == "serve":
        serve(host=args.host, port=args.port)
    else:
        main()
