import datetime
from multiprocessing import Queue
from unittest import skip
from unittest.mock import patch

import dateutil.parser
from django.test import TransactionTestCase

from display.calculators.contestant_processor import ContestantProcessor
from display.calculators.gatekeeper_route import GatekeeperRoute
from display.models import Aeroplane, NavigationTask, Contest, Crew, Contestant, Person, Team, EditableRoute
from display.models.contestant_utility_models import ContestantReceivedPosition
from utilities.mock_utilities import TraccarMock


@patch("display.calculators.contestant_processor.get_traccar_instance", return_value=TraccarMock)
@patch("display.models.contestant.get_traccar_instance", return_value=TraccarMock)
@patch("display.signals.get_traccar_instance", return_value=TraccarMock)
class TestInterpolation(TransactionTestCase):
    @patch("display.calculators.contestant_processor.get_traccar_instance", return_value=TraccarMock)
    @patch("display.models.contestant.get_traccar_instance", return_value=TraccarMock)
    @patch("display.signals.get_traccar_instance", return_value=TraccarMock)
    def setUp(self, *args):
        from display.default_scorecards import default_scorecard_fai_precision_2020

        self.scorecard = default_scorecard_fai_precision_2020.get_default_scorecard()
        with open("display/calculators/tests/NM.csv", "r") as file:
            editable_route, _ = EditableRoute.create_from_csv("Test", file.readlines()[1:])
            route = editable_route.create_precision_route(True, self.scorecard)
        navigation_task_start_time = datetime.datetime(2020, 8, 1, 6, 0, 0).astimezone()
        navigation_task_finish_time = datetime.datetime(2020, 8, 1, 16, 0, 0).astimezone()
        aeroplane = Aeroplane.objects.create(registration="LN-YDB")

        self.navigation_task = NavigationTask.create(
            name="NM navigation_task",
            route=route,
            original_scorecard=self.scorecard,
            contest=Contest.objects.create(
                name="contest",
                start_time=datetime.datetime.now(datetime.timezone.utc),
                finish_time=datetime.datetime.now(datetime.timezone.utc),
                time_zone="Europe/Oslo",
            ),
            start_time=navigation_task_start_time,
            finish_time=navigation_task_finish_time,
        )
        crew = Crew.objects.create(member1=Person.objects.create(first_name="Mister", last_name="Pilot"))
        self.team = Team.objects.create(crew=crew, aeroplane=aeroplane)
        start_time, speed = datetime.datetime(2020, 8, 1, 9, 15, tzinfo=datetime.timezone.utc), 70
        # Required to make the time zone save correctly
        self.navigation_task.refresh_from_db()
        self.contestant = Contestant.objects.create(
            navigation_task=self.navigation_task,
            team=self.team,
            takeoff_time=start_time,
            tracker_start_time=start_time - datetime.timedelta(minutes=30),
            finished_by_time=start_time + datetime.timedelta(hours=2),
            tracker_device_id="Test contestant",
            contestant_number=1,
            minutes_to_starting_point=6,
            air_speed=speed,
            wind_direction=165,
            wind_speed=8,
        )

    def test_no_interpolation(self, *args):
        gatekeeper = ContestantProcessor(self.contestant)
        start_position = ContestantReceivedPosition(
            contestant=self.contestant, time=dateutil.parser.parse("2020-01-01T00:00:00Z"), latitude=60, longitude=11
        )
        gatekeeper.track = [start_position]
        next_position = ContestantReceivedPosition(
            contestant=self.contestant, time=dateutil.parser.parse("2020-01-01T00:00:02Z"), latitude=60, longitude=12
        )
        interpolated = gatekeeper.interpolate_track(start_position, next_position)
        self.assertEqual(1, len(interpolated))
        self.assertEqual(next_position, interpolated[0])

    @skip("Interpolation is disabled?")
    def test_interpolation(self, *args):
        gatekeeper = ContestantProcessor(self.contestant)
        start_position = ContestantReceivedPosition(
            contestant=self.contestant, time=dateutil.parser.parse("2020-01-01T00:00:00Z"), latitude=60, longitude=11
        )
        gatekeeper.track = [start_position]
        next_position = ContestantReceivedPosition(
            contestant=self.contestant, time=dateutil.parser.parse("2020-01-01T00:00:05Z"), latitude=60, longitude=12
        )
        interpolated = gatekeeper.interpolate_track(start_position, next_position)
        expected = [
            ("2020-01-01 00:00:01+00:00", 60.00060459827332, 11.199996353395562),
            ("2020-01-01 00:00:02+00:00", 60.0009069019875, 11.399998176675595),
            ("2020-01-01 00:00:03+00:00", 60.0009069019875, 11.600001823324405),
            ("2020-01-01 00:00:04+00:00", 60.00060459827332, 11.800003646604438),
            ("2020-01-01 00:00:05+00:00", 60, 12),
        ]
        print([f"({item.time.isoformat()}, {item.latitude}, {item.longitude})" for item in interpolated])
        self.assertEqual(5, len(interpolated))
        for index in range(len(interpolated)):
            self.assertEqual(str(interpolated[index].time), expected[index][0])
            self.assertAlmostEqual(interpolated[index].latitude, expected[index][1])
            self.assertAlmostEqual(interpolated[index].longitude, expected[index][2])


@patch("display.calculators.contestant_processor.get_traccar_instance", return_value=TraccarMock)
@patch("display.models.contestant.get_traccar_instance", return_value=TraccarMock)
@patch("display.signals.get_traccar_instance", return_value=TraccarMock)
class TestCrossingEstimate(TransactionTestCase):
    @patch("display.calculators.contestant_processor.get_traccar_instance", return_value=TraccarMock)
    @patch("display.models.contestant.get_traccar_instance", return_value=TraccarMock)
    @patch("display.signals.get_traccar_instance", return_value=TraccarMock)
    def setUp(self, *args):
        from display.default_scorecards import default_scorecard_fai_precision_2020

        self.scorecard = default_scorecard_fai_precision_2020.get_default_scorecard()

        with open("display/calculators/tests/NM.csv", "r") as file:
            editable_route, _ = EditableRoute.create_from_csv("Test", file.readlines()[1:])
            self.route = editable_route.create_precision_route(True, self.scorecard)
        self.route.waypoints[1].time_check = False
        self.route.save()
        navigation_task_start_time = datetime.datetime(2020, 8, 1, 6, 0, 0).astimezone()
        navigation_task_finish_time = datetime.datetime(2020, 8, 1, 16, 0, 0).astimezone()
        aeroplane = Aeroplane.objects.create(registration="LN-YDB")
        self.navigation_task = NavigationTask.create(
            name="NM navigation_task",
            route=self.route,
            original_scorecard=self.scorecard,
            contest=Contest.objects.create(
                name="contest",
                start_time=datetime.datetime.now(datetime.timezone.utc),
                finish_time=datetime.datetime.now(datetime.timezone.utc),
                time_zone="Europe/Oslo",
            ),
            start_time=navigation_task_start_time,
            finish_time=navigation_task_finish_time,
        )
        crew = Crew.objects.create(member1=Person.objects.create(first_name="Mister", last_name="Pilot"))
        self.team = Team.objects.create(crew=crew, aeroplane=aeroplane)
        start_time, speed = datetime.datetime(2020, 8, 1, 9, 15, tzinfo=datetime.timezone.utc), 70
        # Required to make the time zone save correctly
        self.navigation_task.refresh_from_db()
        self.contestant = Contestant.objects.create(
            navigation_task=self.navigation_task,
            team=self.team,
            takeoff_time=start_time,
            tracker_start_time=start_time - datetime.timedelta(minutes=30),
            finished_by_time=start_time + datetime.timedelta(hours=2),
            tracker_device_id="Test contestant",
            contestant_number=1,
            minutes_to_starting_point=6,
            air_speed=speed,
            wind_direction=165,
            wind_speed=8,
        )

    def test_next_is_timed(self, *args):
        # SP, 9.481223867089488, 59.19144317223039, sp, 2
        # SC 1/1, 9.408413335420015, 59.19427817352367, secret, 1.5
        # TP1, 9.198618292991952, 59.20222672648168, tp, 1.5
        gatekeeper = GatekeeperRoute(self.contestant, Queue(), [])
        start_position = ContestantReceivedPosition(
            time=dateutil.parser.parse("2020-01-01T00:00:00Z"),
            latitude=59.19144317223039,
            longitude=9.481523867089488,
            speed=70,
            course=270,
        )
        next_position = ContestantReceivedPosition(
            time=dateutil.parser.parse("2020-01-01T00:00:02Z"),
            latitude=59.19144317223039,
            longitude=9.481623867089488,
            speed=70,
            course=270,
        )
        gatekeeper.track = [start_position, next_position]
        gate, estimated = gatekeeper.estimate_crossing_time_of_next_timed_gate()
        self.assertEqual(self.route.waypoints[0].name, gate.name)
        expected = dateutil.parser.parse("2020-01-01T00:00:02.631887+00:00")
        self.assertEqual(expected, estimated)

    def test_next_is_not_timed(self, *args):
        # SP, 9.481223867089488, 59.19144317223039, sp, 2
        # SC 1/1, 9.408413335420015, 59.19427817352367, secret, 1.5
        # TP1, 9.198618292991952, 59.20222672648168, tp, 1.5
        gatekeeper = GatekeeperRoute(self.contestant, Queue(), [])
        # Skip first gate
        gatekeeper.outstanding_gates.pop(0)
        start_position = ContestantReceivedPosition(
            time=dateutil.parser.parse("2020-01-01T00:00:00Z"),
            latitude=59.19144317223039,
            longitude=9.481523867089488,
            speed=70,
            course=270,
        )
        next_position = ContestantReceivedPosition(
            time=dateutil.parser.parse("2020-01-01T00:00:02Z"),
            latitude=59.19144317223039,
            longitude=9.481623867089488,
            speed=70,
            course=270,
        )
        gatekeeper.track = [start_position, next_position]
        gate, estimated = gatekeeper.estimate_crossing_time_of_next_timed_gate()
        self.assertEqual(self.route.waypoints[2].name, gate.name)
        expected = dateutil.parser.parse("2020-01-01T00:07:22.512632+00:00")
        self.assertEqual(expected, estimated)
