"""Characterization tests for hardware-profile-driven behaviour: native
address validation (digitwin/hardware.py) and the system tags a concrete
PLC model auto-populates from its profile (PLC._define_system_tags).

If a change here is intentional, update the test in the same commit and say
why.
"""

from __future__ import annotations

import time

import pytest

from digitwin.hardware import IEC_DOTTED, AddressArea, AddressError
from digitwin.models import PLC_Generic, PLC_Schneider_TM221CE16T, plc_from_model
from digitwin.plc import (
    ALWAYS_OFF_TAG,
    ALWAYS_ON_TAG,
    FIRST_SCAN_TAG,
    PLC,
    SCAN_TIME_MS_TAG,
    Program,
    TagType,
)


def _plc(program: Program = lambda _plc: None) -> PLC_Schneider_TM221CE16T:
    return PLC_Schneider_TM221CE16T("t", program)


# --- abstract base -----------------------------------------------------


def test_plc_refuses_to_instantiate_without_a_profile() -> None:
    with pytest.raises(TypeError, match="no hardware profile"):
        PLC("t", lambda _plc: None)


# --- address validation: accept/reject the TODO's own examples ---------


def test_tm221_accepts_the_last_valid_input_and_output_channel() -> None:
    plc = _plc()
    plc.define_tag("in8", TagType.DISCRETE_INPUT, False, "%I0.8")
    plc.define_tag("out6", TagType.DISCRETE_OUTPUT, False, "%Q0.6")

    assert plc.tags["in8"].native_address == "%I0.8"
    assert plc.tags["out6"].native_address == "%Q0.6"


def test_tm221_rejects_an_input_channel_past_the_9_di_count() -> None:
    plc = _plc()
    with pytest.raises(AddressError, match="past the discrete input count"):
        plc.define_tag("in9", TagType.DISCRETE_INPUT, False, "%I0.9")


def test_tm221_rejects_an_output_channel_past_the_7_do_count() -> None:
    plc = _plc()
    with pytest.raises(AddressError, match="past the discrete output count"):
        plc.define_tag("out7", TagType.DISCRETE_OUTPUT, False, "%Q0.7")


def test_tm221_rejects_malformed_syntax() -> None:
    plc = _plc()
    with pytest.raises(AddressError, match="not valid IEC_DOTTED syntax"):
        plc.define_tag("bad", TagType.DISCRETE_OUTPUT, False, "%QX0.0")


def test_address_area_must_match_the_tag_type() -> None:
    plc = _plc()
    with pytest.raises(AddressError, match="can't address a discrete_input tag"):
        plc.define_tag("wrong_area", TagType.DISCRETE_INPUT, False, "%Q0.0")


def test_tm221_accepts_both_analog_inputs_and_rejects_a_third() -> None:
    plc = _plc()
    plc.define_tag("ai0", TagType.ANALOG_INPUT, 0, "%IW0.0")
    plc.define_tag("ai1", TagType.ANALOG_INPUT, 0, "%IW0.1")

    with pytest.raises(AddressError, match="past the analog input count"):
        plc.define_tag("ai2", TagType.ANALOG_INPUT, 0, "%IW0.2")


def test_a_word_tag_can_no_longer_claim_an_analog_terminal() -> None:
    # ANALOG_INPUT/ANALOG_OUTPUT are distinct tag types from WORD now that
    # they cross the scan boundary — a WORD tag has no terminal to freeze.
    plc = _plc()
    with pytest.raises(AddressError, match="can't address a word tag"):
        plc.define_tag("wrong_type", TagType.WORD, 0, "%IW0.0")


def test_memory_word_range_is_enforced() -> None:
    plc = _plc()
    plc.define_tag("last_word", TagType.WORD, 0, "%MW7999")  # in range
    with pytest.raises(AddressError, match=r"outside memory words? %0\.\.%7999"):
        plc.define_tag("past_word", TagType.WORD, 0, "%MW8000")


def test_generic_profile_is_permissive_but_still_range_checked() -> None:
    plc = PLC_Generic("t", lambda _plc: None)
    plc.define_tag("in511", TagType.DISCRETE_INPUT, False, "%I0.511")
    with pytest.raises(AddressError):
        plc.define_tag("in512", TagType.DISCRETE_INPUT, False, "%I0.512")


# --- one tag per terminal --------------------------------------------------


def test_a_second_tag_cannot_claim_an_address_already_in_use() -> None:
    plc = _plc()
    plc.define_tag("coil", TagType.DISCRETE_OUTPUT, False, "%Q0.0")
    with pytest.raises(AddressError, match="already assigned to tag 'coil'"):
        plc.define_tag("also_coil", TagType.DISCRETE_OUTPUT, False, "%Q0.0")


def test_unaddressed_tags_do_not_collide() -> None:
    plc = _plc()
    plc.define_tag("a", TagType.INTERNAL_BIT, False)
    plc.define_tag("b", TagType.INTERNAL_BIT, False)  # no address, no conflict

    assert plc.tags["a"].native_address is None
    assert plc.tags["b"].native_address is None


# --- retentive ranges ----------------------------------------------------


def test_is_retentive_reflects_the_profiles_retentive_word_range() -> None:
    profile = PLC_Schneider_TM221CE16T.profile
    assert profile.is_retentive("%MW1000") is True  # inside (0, 1999)
    assert profile.is_retentive("%MW5000") is False  # inside %MW range, not retentive


def test_is_retentive_reflects_the_profiles_retentive_bit_range() -> None:
    profile = PLC_Schneider_TM221CE16T.profile
    assert profile.is_retentive("%M5") is True


def test_a_tag_in_the_retain_range_survives_a_cold_start_without_being_asked() -> None:
    # Retention is a property of the memory area on real hardware, so the
    # profile — not the caller — decides by default.
    plc = _plc()
    plc.define_tag("retained", TagType.INTERNAL_BIT, False, "%M5")  # in (0, 511)
    plc.write("retained", True)

    plc.cold_start()

    assert plc.tags["retained"].retentive is True
    assert plc.read("retained") is True


def test_a_tag_outside_the_retain_range_is_wiped_by_a_cold_start() -> None:
    plc = _plc()
    plc.define_tag("volatile", TagType.WORD, 0, "%MW5000")  # outside (0, 1999)
    plc.write("volatile", 7)

    plc.cold_start()

    assert plc.tags["volatile"].retentive is False
    assert plc.read("volatile") == 0


def test_an_explicit_retentive_flag_overrides_the_profile() -> None:
    plc = _plc()
    plc.define_tag("opt_out", TagType.INTERNAL_BIT, False, "%M5", retentive=False)
    plc.write("opt_out", True)

    plc.cold_start()

    assert plc.read("opt_out") is False


def test_an_unaddressed_tag_defaults_to_non_retentive() -> None:
    plc = _plc()
    plc.define_tag("floating", TagType.INTERNAL_BIT, False)

    assert plc.tags["floating"].retentive is False


def test_generic_declares_no_retain_range_so_nothing_retains_by_default() -> None:
    plc = PLC_Generic("t", lambda _plc: None)
    plc.define_tag("bit", TagType.INTERNAL_BIT, False, "%M5")

    assert plc.tags["bit"].retentive is False


# --- IEC_DOTTED parse/format round-trip ----------------------------------


def test_iec_dotted_round_trips_each_area() -> None:
    for address, area in [
        ("%I0.3", AddressArea.DISCRETE_INPUT),
        ("%Q0.1", AddressArea.DISCRETE_OUTPUT),
        ("%IW0.0", AddressArea.ANALOG_INPUT),
        ("%QW0.0", AddressArea.ANALOG_OUTPUT),
        ("%M12", AddressArea.MEMORY_BIT),
        ("%MW7", AddressArea.MEMORY_WORD),
        ("%S13", AddressArea.SYSTEM_BIT),
        ("%SW4", AddressArea.SYSTEM_WORD),
    ]:
        parsed = IEC_DOTTED.parse(address)
        assert parsed.area is area
        assert IEC_DOTTED.format(parsed) == address


# --- model registry --------------------------------------------------------


def test_plc_from_model_builds_the_registered_class() -> None:
    plc = plc_from_model("TM221CE16T", lambda _plc: None)
    assert isinstance(plc, PLC_Schneider_TM221CE16T)


def test_plc_from_model_rejects_an_unknown_model() -> None:
    with pytest.raises(ValueError, match="unknown PLC model"):
        plc_from_model("NoSuchPLC", lambda _plc: None)


# --- auto-populated system tags -------------------------------------------


def test_first_scan_tag_mirrors_plc_first_scan() -> None:
    seen: list[bool] = []
    plc = _plc(lambda p: seen.append(p.read(FIRST_SCAN_TAG)))

    plc.scan()
    plc.scan()

    assert seen == [True, False]
    assert plc.tags[FIRST_SCAN_TAG].native_address == "%S13"


def test_always_on_and_always_off_bits_hold_constant() -> None:
    plc = _plc()
    plc.scan()

    assert plc.read(ALWAYS_ON_TAG) is True
    assert plc.read(ALWAYS_OFF_TAG) is False
    assert plc.tags[ALWAYS_ON_TAG].native_address == "%S20"
    assert plc.tags[ALWAYS_OFF_TAG].native_address == "%S21"


def test_a_program_cannot_permanently_clobber_always_on() -> None:
    # Real always-on hardware bits ignore a stray write; ours self-heals at
    # the top of every scan instead of forbidding the write outright.
    def clobber_once_then_behave(p: PLC) -> None:
        if p.scan_count == 0:
            p.write(ALWAYS_ON_TAG, False)

    plc = _plc(clobber_once_then_behave)

    plc.scan()
    assert plc.read(ALWAYS_ON_TAG) is False  # clobbered during scan 1...
    plc.scan()
    assert plc.read(ALWAYS_ON_TAG) is True  # ...but reasserted at the top of scan 2


def test_scan_time_word_reflects_the_last_scan_duration_in_ms() -> None:
    plc = _plc(lambda _plc: time.sleep(0.01))
    plc.scan()

    assert plc.read(SCAN_TIME_MS_TAG) >= 10
    assert plc.tags[SCAN_TIME_MS_TAG].native_address == "%SW10"
