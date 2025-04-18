#
import datetime

from display.utilities.clone_object import simple_clone, get_or_none
from display.models import (
    GateScore,
    Scorecard,
)
from display.utilities.gate_definitions import (
    TURNPOINT,
    TAKEOFF_GATE,
    LANDING_GATE,
    STARTINGPOINT,
    UNKNOWN_LEG,
    DUMMY,
    SECRETPOINT,
    FINISHPOINT,
)
from display.utilities.navigation_task_type_definitions import PRECISION


def get_default_scorecard():
    scorecard, created = Scorecard.objects.update_or_create(
        name="FAI Precision 2020 (without procedure turns)",
        defaults={
            "shortcut_name": "FAI Precision no procedure turns",
            "valid_from": datetime.datetime(2022, 1, 1, tzinfo=datetime.timezone.utc),
            "backtracking_penalty": 200,
            "backtracking_grace_time_seconds": 5,
            "use_procedure_turns": False,
            "task_type": [PRECISION],
            "calculator": PRECISION,
            "prohibited_zone_maximum": -1,
            "prohibited_zone_penalty": 0,
            "included_fields": [
                [
                    "Backtracking",
                    "backtracking_penalty",
                    "backtracking_grace_time_seconds",
                ],
                [
                    "Prohibited zone",
                    "prohibited_zone_grace_time",
                    "prohibited_zone_penalty",
                ],
                [
                    "Penalty zone",
                    "penalty_zone_grace_time",
                    "penalty_zone_penalty_per_second",
                    "penalty_zone_maximum",
                ],
                ["Initial score", "initial_score"],
            ],
        },
    )

    regular_gate_score, created = GateScore.objects.update_or_create(
        scorecard=scorecard,
        gate_type=TURNPOINT,
        defaults={
            "extended_gate_width": 6,
            "bad_crossing_extended_gate_penalty": 0,
            "graceperiod_before": 2,
            "graceperiod_after": 2,
            "maximum_penalty": 100,
            "penalty_per_second": 3,
            "missed_penalty": 100,
            "missed_procedure_turn_penalty": 200,
            "backtracking_after_steep_gate_grace_period_seconds": 0,
            "included_fields": [
                [
                    "Penalties",
                    "penalty_per_second",
                    "maximum_penalty",
                    "missed_penalty",
                ],
                ["Time limits", "graceperiod_before", "graceperiod_after"],
            ],
        },
    )

    GateScore.objects.update_or_create(
        scorecard=scorecard,
        gate_type=TAKEOFF_GATE,
        defaults={
            "extended_gate_width": 0,
            "bad_crossing_extended_gate_penalty": 0,
            "graceperiod_before": 0,
            "graceperiod_after": 60,
            "maximum_penalty": 200,
            "penalty_per_second": 200,
            "missed_penalty": 200,
            "missed_procedure_turn_penalty": 0,
            "backtracking_after_steep_gate_grace_period_seconds": 0,
            "included_fields": [
                ["Penalties", "maximum_penalty", "missed_penalty"],
                ["Time limits", "graceperiod_before", "graceperiod_after"],
            ],
        },
    )

    GateScore.objects.update_or_create(
        scorecard=scorecard,
        gate_type=LANDING_GATE,
        defaults={
            "extended_gate_width": 0,
            "bad_crossing_extended_gate_penalty": 0,
            "graceperiod_before": 999999999,
            "graceperiod_after": 60,
            "maximum_penalty": 0,
            "penalty_per_second": 0,
            "missed_penalty": 0,
            "missed_procedure_turn_penalty": 0,
            "backtracking_after_steep_gate_grace_period_seconds": 0,
            "included_fields": [["Penalties", "maximum_penalty", "missed_penalty"]],
        },
    )

    GateScore.objects.update_or_create(
        scorecard=scorecard,
        gate_type=STARTINGPOINT,
        defaults={
            "extended_gate_width": 2,
            "bad_crossing_extended_gate_penalty": 200,
            "graceperiod_before": 2,
            "graceperiod_after": 2,
            "maximum_penalty": 100,
            "penalty_per_second": 3,
            "missed_penalty": 100,
            "missed_procedure_turn_penalty": 200,
            "backtracking_after_steep_gate_grace_period_seconds": 0,
            "included_fields": [
                [
                    "Penalties",
                    "penalty_per_second",
                    "maximum_penalty",
                    "missed_penalty",
                    "bad_crossing_extended_gate_penalty",
                ],
                ["Additional gate sizes", "extended_gate_width"],
                ["Time limits", "graceperiod_before", "graceperiod_after"],
            ],
        },
    )
    simple_clone(
        regular_gate_score,
        {"gate_type": DUMMY},
        existing_clone=get_or_none(scorecard.gatescore_set.filter(gate_type=DUMMY)),
    )
    simple_clone(
        regular_gate_score,
        {"gate_type": UNKNOWN_LEG},
        existing_clone=get_or_none(scorecard.gatescore_set.filter(gate_type=UNKNOWN_LEG)),
    )
    simple_clone(
        regular_gate_score,
        {"gate_type": SECRETPOINT},
        existing_clone=get_or_none(scorecard.gatescore_set.filter(gate_type=SECRETPOINT)),
    )
    simple_clone(
        regular_gate_score,
        {"gate_type": FINISHPOINT},
        existing_clone=get_or_none(scorecard.gatescore_set.filter(gate_type=FINISHPOINT)),
    )

    return scorecard
