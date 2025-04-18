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
    SECRETPOINT,
    FINISHPOINT,
    DUMMY,
    UNKNOWN_LEG,
)
from display.utilities.navigation_task_type_definitions import AIRSPORTS


def get_default_scorecard():
    scorecard, created = Scorecard.objects.update_or_create(
        name="Nordic Air Sports Race",
        defaults={
            "shortcut_name": "Nordic Air Sports Race",
            "valid_from": datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc),
            "backtracking_penalty": 200,
            "backtracking_grace_time_seconds": 5,
            "backtracking_maximum_penalty": 200,
            "use_procedure_turns": False,
            "task_type": [AIRSPORTS],
            "calculator": AIRSPORTS,
            "corridor_maximum_penalty": 100,  # verified
            "corridor_outside_penalty": 1,  # verified
            "corridor_grace_time": 5,  # verified
            "below_minimum_altitude_penalty": 500,  # verified
            "below_minimum_altitude_maximum_penalty": 500,  # verified
            "prohibited_zone_penalty": 50,
            "prohibited_zone_grace_time": 0,
            "prohibited_zone_maximum": 200,
            "penalty_zone_grace_time": 0,
            "penalty_zone_penalty_per_second": 3,
            "penalty_zone_maximum": 200,
            "included_fields": [
                [
                    "Corridor penalties",
                    "corridor_grace_time",
                    "backtracking_penalty",
                    "corridor_outside_penalty",
                ],
                ["Prohibited zone", "prohibited_zone_grace_time", "prohibited_zone_penalty", "prohibited_zone_maximum"],
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

    regular_gate_score = GateScore.objects.update_or_create(
        scorecard=scorecard,
        gate_type=TURNPOINT,
        defaults={
            "extended_gate_width": 0,
            "bad_crossing_extended_gate_penalty": 0,
            "graceperiod_before": 2,
            "graceperiod_after": 2,
            "maximum_penalty": 100,
            "penalty_per_second": 3,
            "missed_penalty": 100,
            "backtracking_after_steep_gate_grace_period_seconds": 0,
            "backtracking_before_gate_grace_period_nm": 0.5,
            "backtracking_after_gate_grace_period_nm": 0.5,
            "missed_procedure_turn_penalty": 0,
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
    )[0]

    GateScore.objects.update_or_create(
        scorecard=scorecard,
        gate_type=TAKEOFF_GATE,
        defaults={
            "extended_gate_width": 0,
            "bad_crossing_extended_gate_penalty": 0,
            "graceperiod_after": 60,  # verified
            "graceperiod_before": 0,
            "maximum_penalty": 200,  # verified
            "backtracking_after_steep_gate_grace_period_seconds": 0,
            "backtracking_after_gate_grace_period_nm": 0.5,
            "penalty_per_second": 200,  # verified
            "missed_penalty": 0,
            "missed_procedure_turn_penalty": 0,
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
            "graceperiod_before": 9999999999,
            "graceperiod_after": 0,
            "backtracking_after_steep_gate_grace_period_seconds": 0,
            "backtracking_after_gate_grace_period_nm": 0.5,
            "maximum_penalty": 0,
            "penalty_per_second": 0,
            "missed_penalty": 0,
            "missed_procedure_turn_penalty": 0,
            "included_fields": [["Penalties", "maximum_penalty", "missed_penalty"]],
        },
    )

    GateScore.objects.update_or_create(
        scorecard=scorecard,
        gate_type=STARTINGPOINT,
        defaults={
            "extended_gate_width": 0.01,
            "bad_crossing_extended_gate_penalty": 0,
            "graceperiod_before": 1,  # verified
            "graceperiod_after": 1,  # verified
            "backtracking_after_steep_gate_grace_period_seconds": 0,
            "backtracking_after_gate_grace_period_nm": 0.5,
            "maximum_penalty": 100,  # verified
            "penalty_per_second": 2,  # verified
            "missed_penalty": 100,  # verified
            "missed_procedure_turn_penalty": 0,
            "included_fields": [
                [
                    "Penalties",
                    "penalty_per_second",
                    "maximum_penalty",
                    "missed_penalty",
                    "bad_crossing_extended_gate_penalty",
                ],
                ["Time limits", "graceperiod_before", "graceperiod_after"],
            ],
        },
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

    return scorecard
