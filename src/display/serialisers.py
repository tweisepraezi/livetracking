import base64
import datetime
import logging
from typing import Optional

import dateutil
from django.core.exceptions import ObjectDoesNotExist
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django_countries.serializer_fields import CountryField
from django_countries.serializers import CountryFieldMixin
from guardian.shortcuts import assign_perm, get_objects_for_user, get_perms, get_user_perms
from rest_framework import serializers
from rest_framework.fields import MultipleChoiceField, SerializerMethodField
from rest_framework.exceptions import ValidationError
from rest_framework.relations import SlugRelatedField
from rest_framework_guardian.serializers import ObjectPermissionsAssignmentMixin
from timezone_field.rest_framework import TimeZoneSerializerField
from phonenumber_field.serializerfields import PhoneNumberField
from phonenumber_field.validators import validate_international_phonenumber

from django.core.exceptions import ValidationError as CoreValidationError

from display.utilities.coordinate_utilities import calculate_distance_lat_lon
from display.utilities.country_code_utilities import get_country_code_from_location, CountryNotFoundException
from display.utilities.route_building_utilities import create_precision_route_from_gpx
from display.models import (
    NavigationTask,
    Aeroplane,
    Team,
    Route,
    Contestant,
    ContestantTrack,
    Scorecard,
    Crew,
    Contest,
    ContestSummary,
    TaskTest,
    Task,
    TaskSummary,
    TeamTestScore,
    Person,
    Club,
    ContestTeam,
    GateScore,
    Prohibited,
    PlayingCard,
    TrackAnnotation,
    ScoreLogEntry,
    GateCumulativeScore,
    EditableRoute,
    MyUser,
    STARTINGPOINT,
    FINISHPOINT,
    GATE_TYPES,
)
from display.waypoint import Waypoint

logger = logging.getLogger(__name__)


class UserSerialiser(serializers.ModelSerializer):
    class Meta:
        model = MyUser
        fields = ("first_name", "last_name", "email")


class MangledEmailField(serializers.Field):
    def to_representation(self, value):
        """
        Serialize the value's class name.
        """
        name, domain = value.split("@")
        levels = domain.split(".")
        return f"{name}@*****.{'.'.join(levels[1:])}"


class AeroplaneSerialiser(serializers.ModelSerializer):
    class Meta:
        model = Aeroplane
        fields = "__all__"


class PersonSignUpSerialiser(serializers.ModelSerializer):
    email = MangledEmailField(read_only=True)

    class Meta:
        model = Person
        fields = ("id", "first_name", "last_name", "email")


class PersonLtdSerialiser(serializers.ModelSerializer):

    class Meta:
        model = Person
        fields = ("first_name", "last_name", "picture")


class ReadWriteSerializerMethodField(serializers.Field):
    def __init__(self, method_name=None, **kwargs):
        self.method_name = method_name
        kwargs["source"] = "*"
        # kwargs['read_only'] = True
        super(ReadWriteSerializerMethodField, self).__init__(**kwargs)

    def bind(self, field_name, parent):
        self.field_name = field_name
        # In order to enforce a consistent style, we error if a redundant
        # 'method_name' argument has been used. For example:
        # my_field = serializer.SerializerMethodField(method_name='get_my_field')
        default_method_name = "get_{field_name}".format(field_name=field_name)
        assert self.method_name != default_method_name, (
            "It is redundant to specify `%s` on SerializerMethodField '%s' in "
            "serializer '%s', because it is the same as the default method name. "
            "Remove the `method_name` argument." % (self.method_name, field_name, parent.__class__.__name__)
        )

        # The method name should default to `get_{field_name}`.
        if self.method_name is None:
            self.method_name = default_method_name

        super(ReadWriteSerializerMethodField, self).bind(field_name, parent)

    def to_representation(self, value):
        method = getattr(self.parent, self.method_name)
        return method(value)

    def to_internal_value(self, data):
        return {self.field_name: data}


class PersonSerialiser(CountryFieldMixin, serializers.ModelSerializer):
    """
    This should only be used in UserPersonViewSet where it is guaranteed that you only get access to your own profile.
    We do not wish to expose apt tracking ID and simulator tracking ID to 3rd persons.
    """

    country_flag_url = serializers.CharField(max_length=200, required=False, read_only=True)
    country = CountryField(required=False)
    # These are required for the app registration to work correctly
    phone_national_number = ReadWriteSerializerMethodField()
    phone_country_prefix = ReadWriteSerializerMethodField()

    def get_phone_national_number(self, obj):
        return str(obj.phone.national_number) if obj.phone is not None else ""

    def get_phone_country_prefix(self, obj):
        return f"+{obj.phone.country_code}" if obj.phone is not None else ""

    def create(self, validated_data):
        country_prefix = validated_data.pop("phone_country_prefix", None)
        phone_national_number = validated_data.pop("phone_national_number", None)
        instance = super().create(validated_data)
        if country_prefix is not None and phone_national_number is not None:
            instance.phone = country_prefix + phone_national_number
            self.validate_phone(instance.phone)
            instance.save()
        return instance

    def update(self, instance, validated_data):
        country_prefix = validated_data.pop("phone_country_prefix", None)
        phone_national_number = validated_data.pop("phone_national_number", None)
        instance = super().update(instance, validated_data)
        if country_prefix is not None and phone_national_number is not None:
            instance.phone = country_prefix + phone_national_number
            self.validate_phone(instance.phone)
            instance.save()
        return instance

    def validate_phone(self, phone):
        validate_international_phonenumber(phone)

    class Meta:
        model = Person
        fields = "__all__"
        # exclude = ("phone",)


class PersonSerialiserExcludingTracking(CountryFieldMixin, serializers.ModelSerializer):
    phone = PhoneNumberField(required=False)

    class Meta:
        model = Person
        exclude = ("app_tracking_id", "simulator_tracking_id")


class ClubSerialiser(CountryFieldMixin, serializers.ModelSerializer):
    country_flag_url = serializers.CharField(max_length=200, required=False, read_only=True)
    country = CountryField(required=False)

    class Meta:
        model = Club
        fields = "__all__"


class CrewSerialiser(serializers.ModelSerializer):
    member1 = PersonSerialiserExcludingTracking()
    member2 = PersonSerialiserExcludingTracking(required=False)

    class Meta:
        model = Crew
        fields = "__all__"

    def create(self, validated_data):
        member1 = validated_data.pop("member1")
        member1_object = Person.get_or_create(
            member1["first_name"], member1["last_name"], member1.get("phone"), member1.get("email")
        )
        member2 = validated_data.pop("member2", None)
        member2_object = None
        if member2:
            member2_object = Person.get_or_create(
                member2["first_name"], member2["last_name"], member2.get("phone"), member2.get("email")
            )
        crew, _ = Crew.objects.get_or_create(member1=member1_object, member2=member2_object)
        return crew

    def update(self, instance, validated_data):
        return self.create(validated_data)


class TeamNestedSerialiser(CountryFieldMixin, serializers.ModelSerializer):
    country_flag_url = serializers.CharField(max_length=200, required=False, read_only=True)
    aeroplane = AeroplaneSerialiser()
    country = CountryField(required=False)
    crew = CrewSerialiser()
    club = ClubSerialiser(required=False)

    class Meta:
        model = Team
        fields = "__all__"

    def create(self, validated_data):
        aeroplane, crew, club = self.nested_update(validated_data)
        team, _ = Team.objects.get_or_create(crew=crew, aeroplane=aeroplane, club=club, defaults=validated_data)
        return team

    def update(self, instance: Team, validated_data):
        instance.aeroplane, instance.crew, instance.club = self.nested_update(validated_data)
        instance.save()
        return instance

    @staticmethod
    def nested_update(validated_data):
        aeroplane_data = validated_data.pop("aeroplane")
        aeroplane_instance = Aeroplane.objects.filter(registration=aeroplane_data.get("registration")).first()
        aeroplane_serialiser = AeroplaneSerialiser(instance=aeroplane_instance, data=aeroplane_data)
        aeroplane_serialiser.is_valid()
        aeroplane = aeroplane_serialiser.save()
        crew_data = validated_data.pop("crew")
        crew_instance = Crew.objects.filter(pk=crew_data.get("id")).first()
        crew_serialiser = CrewSerialiser(instance=crew_instance, data=crew_data)
        crew_serialiser.is_valid()
        crew = crew_serialiser.save()
        club = None
        club_data = validated_data.pop("club", None)
        if club_data:
            club_instance = Club.objects.filter(name=club_data.get("name")).first()
            club_serialiser = ClubSerialiser(instance=club_instance, data=club_data)
            club_serialiser.is_valid()
            club = club_serialiser.save()
        return aeroplane, crew, club


class ContestSummaryNestedSerialiser(serializers.ModelSerializer):
    team = TeamNestedSerialiser()

    class Meta:
        model = ContestSummary
        fields = "__all__"


class NavigationTasksSummarySerialiser(serializers.ModelSerializer):
    class Meta:
        model = NavigationTask
        fields = ("pk", "name", "start_time", "finish_time", "tracking_link")


class NavigationTasksSummaryParticipationSerialiser(serializers.ModelSerializer):
    future_contestants = SerializerMethodField("get_future_contestants")

    class Meta:
        model = NavigationTask
        fields = ("pk", "name", "start_time", "finish_time", "tracking_link", "future_contestants")

    def get_future_contestants(self, navigation_task):
        person = get_object_or_404(Person, email=self.context["request"].user.email)
        future_contestants = navigation_task.contestant_set.filter(
            team__crew__member1=person, finished_by_time__gt=datetime.datetime.now(datetime.timezone.utc)
        )
        serialiser = ContestantSerialiser(future_contestants, many=True, read_only=True)
        return serialiser.data


class ContestFrontEndSerialiser(ObjectPermissionsAssignmentMixin, CountryFieldMixin, serializers.ModelSerializer):
    editors = UserSerialiser(many=True)
    number_of_tasks = serializers.SerializerMethodField("get_number_of_tasks")
    share_string = serializers.CharField(read_only=True)
    is_editor = serializers.SerializerMethodField("get_is_editor")

    class Meta:
        model = Contest
        fields = ("id", "name", "editors", "start_time", "finish_time", "number_of_tasks", "share_string", "is_editor")

    def get_number_of_tasks(self, contest):
        return contest.navigationtask__count

    def get_is_editor(self, contest):
        return "change_contest" in get_user_perms(self.context["request"].user, contest)


class ContestSerialiser(ObjectPermissionsAssignmentMixin, CountryFieldMixin, serializers.ModelSerializer):
    time_zone = TimeZoneSerializerField(required=True)
    navigationtask_set = SerializerMethodField("get_visiblenavigationtasks")
    contest_team_count = serializers.IntegerField(read_only=True)
    share_string = serializers.CharField(read_only=True)
    country_flag_url = serializers.CharField(max_length=200, required=False, read_only=True)
    country = CountryField(required=False)
    registered = SerializerMethodField("get_registered")
    latitude = serializers.FloatField(read_only=True)
    longitude = serializers.FloatField(read_only=True)
    is_editor = serializers.SerializerMethodField("get_is_editor")

    def get_is_editor(self, contest):
        return "change_contest" in get_user_perms(self.context["request"].user, contest)

    def validate(self, validated_data):
        try:
            validated_data["country"] = get_country_code_from_location(
                *[float(x) for x in validated_data["location"].split(",")]
            )
        except CountryNotFoundException:
            raise serializers.ValidationError(
                f"The contest location {validated_data['location']} is not in a valid country",
                code="invalid",
            )
        except:
            pass
        return validated_data

    class Meta:
        model = Contest
        fields = "__all__"

    def get_permissions_map(self, created):
        user = self.context["request"].user
        return {"change_contest": [user], "delete_contest": [user], "view_contest": [user]}

    def get_visiblenavigationtasks(self, contest):
        user = self.context["request"].user
        viewable_contest = user.has_perm("display.view_contest", contest)
        items = filter(
            lambda task: viewable_contest or (task.is_public and contest.is_public and task.is_featured),
            contest.navigationtask_set.all(),
        )
        serialiser = NavigationTasksSummarySerialiser(items, many=True, read_only=True)
        return serialiser.data

    def get_registered(self, contest):
        user = self.context["request"].user
        if not hasattr(user, "email"):
            return False
        return (
            user
            and contest.contest_teams.filter(
                Q(crew__member1__email=user.email) | Q(crew__member2__email=user.email)
            ).exists()
        )

    def create(self, validated_data):
        instance: Contest = super().create(validated_data)
        instance.initialise(self.context["request"].user)
        return instance


class ContestParticipationSerialiser(ContestSerialiser):
    def get_visiblenavigationtasks(self, contest):
        user = self.context["request"].user
        viewable_contest = user.has_perm("display.view_contest", contest)
        items = filter(
            lambda task: task.allow_self_management
            and (viewable_contest or (task.is_public and contest.is_public and task.is_featured)),
            contest.navigationtask_set.all(),
        )
        serialiser = NavigationTasksSummaryParticipationSerialiser(
            items, many=True, read_only=True, context={"request": self.context["request"]}
        )
        return serialiser.data


class SelfManagementSerialiser(serializers.Serializer):
    starting_point_time = serializers.DateTimeField()
    contest_team = serializers.PrimaryKeyRelatedField(queryset=ContestTeam.objects.all())
    adaptive_start = serializers.BooleanField(required=False)
    wind_speed = serializers.FloatField(validators=[MaxValueValidator(40), MinValueValidator(0)])
    wind_direction = serializers.FloatField(validators=[MaxValueValidator(360), MinValueValidator(0)])


class WaypointSerialiser(serializers.Serializer):
    def create(self, validated_data):
        pass

    def update(self, instance, validated_data):
        pass

    name = serializers.CharField(max_length=200)
    latitude = serializers.FloatField(help_text="degrees")
    longitude = serializers.FloatField(help_text="degrees")
    elevation = serializers.FloatField(help_text="Metres above MSL")
    width = serializers.FloatField(help_text="Width of the gate in NM")
    gate_line = serializers.JSONField(
        help_text="Coordinates that describe the starting point and finish point of the gate line, e.g. [[lat1,lon2],[lat2,lon2]"
    )
    gate_line_extended = serializers.JSONField(
        help_text="Coordinates that describe the starting point and finish point of the extended gate line, e.g. [[lat1,lon2],[lat2,lon2]",
        required=False,
    )
    time_check = serializers.BooleanField()
    gate_check = serializers.BooleanField()
    end_curved = serializers.BooleanField()
    type = serializers.CharField(max_length=50, help_text="The type of the gate (tp, sp, fp, to, ldg, secret)")
    distance_next = serializers.FloatField(help_text="Distance to the next gate (NM)")
    distance_previous = serializers.FloatField(help_text="Distance from the previous gate (NM)")
    bearing_next = serializers.FloatField(help_text="True track to the next gate (degrees)")
    bearing_from_previous = serializers.FloatField(help_text="True track from the previous gates to this")
    procedure_turn_points = serializers.JSONField(
        help_text="Curve that describes the procedure turn (read-only)", required=False, read_only=True
    )
    is_procedure_turn = serializers.BooleanField()
    outside_distance = serializers.FloatField(
        help_text="The distance at which we leave the gate vicinity", read_only=True, required=False
    )
    inside_distance = serializers.FloatField(
        help_text="The distance at which we enter the gate vicinity", read_only=True, required=False
    )

    left_corridor_line = serializers.JSONField(
        required=False,
        help_text="A list of (lat,lon) values that define the left edge of the corridor at this waypoint (can be a single point or multiple points e.g. for a curved corridor)",
    )
    right_corridor_line = serializers.JSONField(
        required=False,
        help_text="A list of (lat,lon) values that define the left edge of the corridor at this waypoint (can be a single point or multiple points e.g. for a curved corridor)",
    )
    outer_corner_position = serializers.JSONField(required=False)


class ProhibitedSerialiser(serializers.ModelSerializer):
    path = serializers.JSONField()

    class Meta:
        model = Prohibited
        fields = "__all__"


class RouteSerialiser(serializers.ModelSerializer):
    waypoints = WaypointSerialiser(many=True)
    landing_gates = WaypointSerialiser(required=False, help_text="Optional landing gate", many=True)
    takeoff_gates = WaypointSerialiser(required=False, help_text="Optional takeoff gate", many=True)
    prohibited_set = ProhibitedSerialiser(many=True, required=False)

    class Meta:
        model = Route
        fields = "__all__"

    @staticmethod
    def _create_waypoint(waypoint_data) -> Waypoint:
        waypoint = Waypoint(waypoint_data["name"])
        waypoint.latitude = waypoint_data["latitude"]
        waypoint.longitude = waypoint_data["longitude"]
        waypoint.elevation = waypoint_data["elevation"]
        waypoint.gate_line = waypoint_data["gate_line"]
        waypoint.width = waypoint_data["width"]
        waypoint.time_check = waypoint_data["time_check"]
        waypoint.gate_check = waypoint_data["gate_check"]
        waypoint.end_curved = waypoint_data["end_curved"]
        waypoint.type = waypoint_data["type"]
        waypoint.distance_next = waypoint_data["distance_next"]
        waypoint.distance_previous = waypoint_data["distance_previous"]
        waypoint.bearing_next = waypoint_data["bearing_next"]
        waypoint.bearing_from_previous = waypoint_data["bearing_from_previous"]
        waypoint.is_procedure_turn = waypoint_data["is_procedure_turn"]

        # waypoint.inside_distance = waypoint_data["inside_distance"]
        # waypoint.outside_distance = waypoint_data["outside_distance"]
        return waypoint

    def create(self, validated_data):
        waypoints = []
        for waypoint_data in validated_data.pop("waypoints"):
            waypoints.append(self._create_waypoint(waypoint_data))
        route = Route.objects.create(
            waypoints=waypoints,
            landing_gates=[self._create_waypoint(data) for data in validated_data.pop("landing_gates")],
            takeoff_gates=[self._create_waypoint(data) for data in validated_data.pop("takeoff_gates")],
            **validated_data,
        )
        return route

    def update(self, instance, validated_data):
        waypoints = []
        for waypoint_data in validated_data.pop("waypoints"):
            waypoints.append(self._create_waypoint(waypoint_data))
        instance.waypoints = waypoints
        instance.landing_gates = [self._create_waypoint(data) for data in validated_data.pop("landing_gates")]
        instance.takeoff_gates = [self._create_waypoint(data) for data in validated_data.pop("takeoff_gates")]
        return instance


class ContestTeamSerialiser(serializers.ModelSerializer):
    class Meta:
        model = ContestTeam
        fields = "__all__"

    def validate_team(self, team: Team):
        if not team.crew.member1.has_user and (not team.crew.member2 or not team.crew.member2.has_user):
            raise ValidationError(f"The team {team} is not tied to any registered user")
        return team


class SignupSerialiser(serializers.Serializer):
    def update(self, instance, validated_data):
        request = self.context["request"]
        contest = self.context["contest"]  # type: Contest

        contest_team = validated_data["contest_team"]
        original_team = contest_team.team
        teams = ContestTeam.objects.filter(
            Q(team__crew__member1=request.user.person.pk) | Q(team__crew__member2=request.user.person.pk),
            contest=contest,
        ).exclude(pk=contest_team.pk)
        if teams.exists():
            raise ValidationError(
                f"You are already signed up to the contest {contest} in a different team: f{[str(item) for item in teams]}"
            )
        if validated_data["copilot_id"]:
            teams = ContestTeam.objects.filter(
                Q(team__crew__member1=validated_data["copilot_id"])
                | Q(team__crew__member2=validated_data["copilot_id"]),
                contest=contest,
            ).exclude(pk=contest_team.pk)
            if teams.exists():
                raise ValidationError(
                    f"The co-pilot is already signed up to the contest {contest} in a different team: f{[str(item) for item in teams]}"
                )

        team = Team.get_or_create_from_signup(
            self.context["request"].user,
            validated_data["copilot_id"],
            validated_data["aircraft_registration"],
            validated_data["club_name"],
        )
        new_contest_team = contest.replace_team(original_team, team, {"air_speed": validated_data["airspeed"]})

        return new_contest_team

    def create(self, validated_data):
        request = self.context["request"]
        team = Team.get_or_create_from_signup(
            self.context["request"].user,
            validated_data["copilot_id"],
            validated_data["aircraft_registration"],
            validated_data["club_name"],
        )

        contest = self.context["contest"]
        if ContestTeam.objects.filter(contest=contest, team=team).exists():
            raise ValidationError(f"Team {team} is already registered for contest {contest}")
        teams = ContestTeam.objects.filter(
            Q(team__crew__member1_id=request.user.person.pk) | Q(team__crew__member2_id=request.user.person.pk),
            contest=contest,
        )
        if teams.exists():
            raise ValidationError(
                f"You are already signed up to the contest {contest} in a different team: f{[str(item) for item in teams]}"
            )
        if validated_data["copilot_id"]:
            teams = ContestTeam.objects.filter(
                Q(team__crew__member1=validated_data["copilot_id"])
                | Q(team__crew__member2=validated_data["copilot_id"]),
                contest=contest,
            )
            if teams.exists():
                raise ValidationError(
                    f"The co-pilot is already signed up to the contest {contest} in a different team: f{[str(item) for item in teams]}"
                )
        return contest.replace_team(None, team, {"air_speed": validated_data["airspeed"]})

    aircraft_registration = serializers.CharField()
    club_name = serializers.CharField()
    copilot_id = serializers.PrimaryKeyRelatedField(queryset=Person.objects.all(), required=False, allow_null=True)
    airspeed = serializers.FloatField()
    contest_team = serializers.PrimaryKeyRelatedField(queryset=ContestTeam.objects.all(), required=False)

    def validate_copilot_id(self, value):
        request = self.context["request"]
        my_person = Person.objects.get(email=request.user.email)
        if my_person == value:
            raise ValidationError("You cannot choose yourself as co-pilot")
        return value


class ContestTeamManagementSerialiser(serializers.ModelSerializer):
    contest = ContestParticipationSerialiser(read_only=True)
    team = TeamNestedSerialiser(read_only=True)
    can_edit = serializers.BooleanField(read_only=True)

    class Meta:
        model = ContestTeam
        fields = "__all__"


class ContestTeamNestedSerialiser(serializers.ModelSerializer):
    team = TeamNestedSerialiser()

    class Meta:
        model = ContestTeam
        fields = "__all__"


class ScorecardSerialiser(serializers.ModelSerializer):
    task_type = serializers.SerializerMethodField()

    class Meta:
        model = Scorecard
        fields = ("name", "task_type")

    def get_task_type(self, instance):
        return instance.task_type


class GateScoreSerialiser(serializers.ModelSerializer):
    class Meta:
        model = GateScore
        exclude = ("id", "scorecard", "included_fields")


class ScorecardNestedSerialiser(serializers.ModelSerializer):
    gatescore_set = GateScoreSerialiser(many=True)
    corridor_width = serializers.FloatField(read_only=True)
    task_type = serializers.SerializerMethodField()

    class Meta:
        model = Scorecard
        read_only_fields = ["task_type"]
        exclude = ("id", "original", "included_fields", "calculator", "name", "use_procedure_turns")

    def get_task_type(self, instance):
        return instance.task_type

    def create(self, validated_data):
        raise NotImplementedError("Manually creating scorecards is not supported")

    def update(self, instance, validated_data):
        gate_scores = validated_data.pop("gatescore_set")
        Scorecard.objects.filter(pk=instance.pk).update(**validated_data)
        instance.refresh_from_db()
        for gate in gate_scores:
            instance.gatescore_set.filter(gate_type=gate["gate_type"]).update(**gate)
        return instance


class DangerLevelSerialiser(serializers.Serializer):
    danger_level = serializers.FloatField()
    accumulated_score = serializers.FloatField()

    def create(self, validated_data):
        pass

    def update(self, instance, validated_data):
        pass


class GateScoreIfCrossedNowSerialiser(serializers.Serializer):
    seconds_to_planned_crossing = serializers.FloatField()
    estimated_crossing_offset = serializers.FloatField()
    estimated_score = serializers.FloatField()
    waypoint_name = serializers.CharField()
    final = serializers.BooleanField(required=False)
    missed = serializers.BooleanField(required=False)

    def create(self, validated_data):
        pass

    def update(self, instance, validated_data):
        pass


class PositionSerialiser(serializers.Serializer):
    """
    {
        "0": {
            "time": "2015-01-01T07:15:54Z",
            "altitude": 177.7005608388,
            "battery_level": 1,
            "contestant": "310",
            "course": 0,
            "device_id": "2017_101",
            "latitude": 48.10305,
            "longitude": 16.93245,
            "navigation_task": "31",
            "speed": 0
        }
    }
    """

    def create(self, validated_data):
        pass

    def update(self, instance, validated_data):
        pass

    latitude = serializers.FloatField()
    longitude = serializers.FloatField()
    altitude = serializers.FloatField()
    time = serializers.DateTimeField()
    progress = serializers.FloatField()
    device_id = serializers.CharField()
    position_id = serializers.IntegerField()


class GpxTrackSerialiser(serializers.Serializer):
    def update(self, instance, validated_data):
        pass

    def create(self, validated_data):
        pass

    track_file = serializers.CharField(write_only=True, required=True, help_text="Base64 encoded gpx track file")

    def validate_track_file(self, value):
        if value:
            try:
                base64.decodebytes(bytes(value, "utf-8"))
            except Exception as e:
                raise ValidationError("track_file must be in a valid base64 string format.")
        return value


class ContestantTrackWithTrackPointsSerialiser(serializers.ModelSerializer):
    """
    Used for output to the frontend
    """

    track = PositionSerialiser(many=True, read_only=True)

    class Meta:
        model = ContestantTrack
        fields = "__all__"


class PlayingCardSerialiser(serializers.ModelSerializer):
    class Meta:
        model = PlayingCard
        fields = "__all__"


class SharingSerialiser(serializers.Serializer):
    def create(self, validated_data):
        pass

    def update(self, instance, validated_data):
        pass

    PUBLIC = "public"
    PRIVATE = "private"
    UNLISTED = "unlisted"
    VISIBILITIES = ((PUBLIC, "Public"), (PRIVATE, "Private"), (UNLISTED, "Unlisted"))
    visibility = serializers.ChoiceField(choices=VISIBILITIES)


class ContestantTrackSerialiser(serializers.ModelSerializer):
    """
    Used for output to the frontend
    """

    contest_summary = serializers.FloatField(read_only=True, required=False)

    class Meta:
        model = ContestantTrack
        fields = "__all__"


class ContestantSerialiser(serializers.ModelSerializer):
    class Meta:
        model = Contestant
        exclude = ("navigation_task", "predefined_gate_times")

    gate_times = serializers.JSONField(
        help_text="Dictionary where the keys are gate names (must match the gate names in the route file) and the "
        "values are $date-time strings (with time zone). Missing values will be populated from internal "
        "calculations.",
        required=False,
    )
    scorecard_rules = serializers.JSONField(help_text="Dictionary with all rules", read_only=True)
    tracker_id_display = serializers.JSONField(help_text="", read_only=True)
    default_map_url = SerializerMethodField("get_default_map_url", read_only=True)
    has_crossed_starting_line = serializers.BooleanField(read_only=True)

    def get_default_map_url(self, contestant):
        return reverse("contestant_default_map", kwargs={"pk": contestant.pk})

    def create(self, validated_data):
        try:
            navigation_task = self.context["navigation_task"]
        except KeyError:
            raise Http404("Navigation task not found")
        validated_data["navigation_task"] = navigation_task
        validated_data["gate_times"] = {
            key: dateutil.parser.parse(value) for key, value in validated_data["gate_times"].items()
        }
        # gate_times = validated_data.pop("gate_times", {})
        team = validated_data["team"]
        if contest_team := ContestTeam.objects.filter(contest=navigation_task.contest, team=team).first():
            if (
                "tracking_service" not in validated_data
                or validated_data["tracking_service"] is None
                or validated_data["tracking_service"] == ""
            ):
                validated_data["tracking_service"] = contest_team.tracking_service
            if (
                "tracking_device" not in validated_data
                or validated_data["tracking_device"] is None
                or validated_data["tracking_device"] == ""
            ):
                validated_data["tracking_device"] = contest_team.tracking_device
            if (
                "tracker_device_id" not in validated_data
                or validated_data["tracker_device_id"] is None
                or validated_data["tracker_device_id"] == ""
            ):
                validated_data["tracker_device_id"] = contest_team.tracker_device_id
            if (
                "air_speed" not in validated_data
                or validated_data["air_speed"] is None
                or validated_data["air_speed"] == ""
            ):
                validated_data["air_speed"] = contest_team.air_speed

        contestant = Contestant.objects.create(**validated_data)
        # contestant.gate_times = {key: dateutil.parser.parse(value) for key, value in gate_times.items()}
        # contestant.save()
        if not ContestTeam.objects.filter(contest=contestant.navigation_task.contest, team=contestant.team).exists():
            ContestTeam.objects.create(
                contest=contestant.navigation_task.contest,
                team=contestant.team,
                tracker_device_id=contestant.tracker_device_id,
                tracking_service=contestant.tracking_service,
                air_speed=contestant.air_speed,
            )

        return contestant

    def update(self, instance, validated_data):
        gate_times = validated_data.pop("gate_times", {})
        if not self.partial:
            team = validated_data["team"]
            if contest_team := ContestTeam.objects.filter(contest=instance.navigation_task.contest, team=team).first():
                if (
                    "tracking_service" not in validated_data
                    or validated_data["tracking_service"] is None
                    or validated_data["tracking_service"] == ""
                ):
                    validated_data["tracking_service"] = contest_team.tracking_service
                if (
                    "tracking_device" not in validated_data
                    or validated_data["tracking_device"] is None
                    or validated_data["tracking_device"] == ""
                ):
                    validated_data["tracking_device"] = contest_team.tracking_device
                if (
                    "tracker_device_id" not in validated_data
                    or validated_data["tracker_device_id"] is None
                    or validated_data["tracker_device_id"] == ""
                ):
                    validated_data["tracker_device_id"] = contest_team.tracker_device_id
                if (
                    "air_speed" not in validated_data
                    or validated_data["air_speed"] is None
                    or validated_data["air_speed"] == ""
                ):
                    validated_data["air_speed"] = contest_team.air_speed
        Contestant.objects.filter(pk=instance.pk).update(**validated_data)
        instance.refresh_from_db()
        instance.gate_times = {key: dateutil.parser.parse(value) for key, value in gate_times.items()}
        instance.save()
        ContestTeam.objects.update_or_create(
            defaults={
                "tracker_device_id": instance.tracker_device_id,
                "tracking_service": instance.tracking_service,
                "tracking_device": instance.tracking_device,
                "air_speed": instance.air_speed,
            },
            contest=instance.navigation_task.contest,
            team=instance.team,
        )
        return instance


class OngoingNavigationSerialiser(serializers.ModelSerializer):
    contest = ContestSerialiser(read_only=True)
    active_contestants = SerializerMethodField("get_active_contestants")

    class Meta:
        model = NavigationTask
        fields = ("pk", "name", "start_time", "finish_time", "tracking_link", "active_contestants", "contest")

    def get_active_contestants(self, navigation_task):
        future_contestants = navigation_task.contestant_set.filter(
            contestanttrack__calculator_started=True, contestanttrack__calculator_finished=False
        )
        serialiser = ContestantSerialiser(future_contestants, many=True, read_only=True)
        return serialiser.data


class FilteredContestantNestedTeamSerialiser(serializers.ListSerializer):
    def to_representation(self, data):
        if selected_contestants := self.context.get("selected_contestants"):
            logger.debug(f"Filtering contestants {selected_contestants}")
            if len(selected_contestants) > 0:
                data = data.filter(pk__in=selected_contestants)
        return super().to_representation(data)


class ContestantNestedTeamSerialiser(ContestantSerialiser):
    """
    Contestants. When putting or patching, note that the entire team has to be specified for it to be changed.
    Otherwise changes will be ignored.
    """

    team = TeamNestedSerialiser()

    class Meta:
        model = Contestant
        list_serializer_class = FilteredContestantNestedTeamSerialiser
        exclude = ("navigation_task", "predefined_gate_times")

    def create(self, validated_data):
        team_data = validated_data.pop("team")
        team_serialiser = TeamNestedSerialiser(data=team_data)
        team_serialiser.is_valid()
        team = team_serialiser.save()
        validated_data["team"] = team
        return super().create(validated_data)

    def update(self, instance, validated_data):
        team_data = validated_data.pop("team", None)
        if team_data:
            try:
                team_instance = Team.objects.get(pk=team_data.get("id"))
            except ObjectDoesNotExist:
                team_instance = None
            team_serialiser = TeamNestedSerialiser(instance=team_instance, data=team_data)
            team_serialiser.is_valid()
            team = team_serialiser.save()
            validated_data.update({"team": team.pk})
        return super().update(instance, validated_data)


class ContestantNestedTeamSerialiserWithContestantTrack(ContestantNestedTeamSerialiser):
    contestanttrack = ContestantTrackSerialiser(read_only=True)


class NavigationTaskNestedTeamRouteSerialiser(serializers.ModelSerializer):
    contestant_set = ContestantNestedTeamSerialiserWithContestantTrack(many=True, read_only=True)
    original_scorecard = SlugRelatedField(
        slug_field="shortcut_name",
        queryset=Scorecard.get_originals(),
        required=False,
        help_text="Reference to an existing scorecard name. This forms the basis for the values available in the 'scorecard' field. Currently existing scorecards: {}".format(
            # ", ".join(["'{}'".format(item) for item in Scorecard.get_originals()])
            ""
        ),
    )
    scorecard = ScorecardNestedSerialiser(read_only=True)
    display_contestant_rank_summary = serializers.BooleanField(read_only=True)
    share_string = serializers.CharField(read_only=True)
    route = RouteSerialiser()
    time_zone = TimeZoneSerializerField(source="contest.time_zone", read_only=True)
    contest = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = NavigationTask
        fields = "__all__"

    def create(self, validated_data):
        user = self.context["request"].user
        contestant_set = validated_data.pop("contestant_set", [])
        try:
            validated_data["contest"] = self.context["contest"]
        except KeyError:
            raise Http404("Contest not found")

        route = validated_data.pop("route", None)
        route_serialiser = RouteSerialiser(data=route)
        route_serialiser.is_valid()
        route = route_serialiser.save()
        assign_perm("view_route", user, route)
        assign_perm("delete_route", user, route)
        assign_perm("change_route", user, route)
        navigation_task = NavigationTask.create(**validated_data, route=route)
        for contestant_data in contestant_set:
            contestant_serialiser = ContestantNestedTeamSerialiser(
                data=contestant_data, context={"navigation_task": navigation_task}
            )
            contestant_serialiser.is_valid()
            contestant_serialiser.save()
        return navigation_task


class MyRoutesField(serializers.PrimaryKeyRelatedField):
    def get_queryset(self):
        user = self.context["request"].user
        return EditableRoute.get_for_user(user)


class NavigationTaskEditableRoutReferenceSerialiser(serializers.ModelSerializer):
    original_scorecard = SlugRelatedField(
        slug_field="shortcut_name",
        queryset=Scorecard.get_originals(),
        required=True,
        help_text="Reference to an existing scorecard name. Use the shortcut_name to reference the scorecard",
    )
    editable_route = MyRoutesField(queryset=EditableRoute.objects.all())
    corridor_width = serializers.FloatField(
        help_text="If the task type is ANR, air sports race, or air sports challenge, this value must be set.",
        write_only=True,
        required=False,
    )
    rounded_corners = serializers.BooleanField(
        help_text="If the task type is ANR, air sports race, or air sports challenge, this value must be set.",
        write_only=True,
        required=False,
    )

    class Meta:
        model = NavigationTask
        exclude = ("route", "contest", "scorecard")

    def create(self, validated_data):
        with transaction.atomic():
            editable_route: EditableRoute = validated_data["editable_route"]
            original_scorecard: Scorecard = validated_data["original_scorecard"]
            try:
                route = editable_route.create_route(
                    original_scorecard.calculator,
                    original_scorecard,
                    validated_data.pop("rounded_corners", None),
                    validated_data.pop("corridor_width", None),
                )
            except CoreValidationError as e:
                raise ValidationError(e)
            user = self.context["request"].user
            try:
                validated_data["contest"] = self.context["contest"]
            except KeyError:
                raise Http404("Contest not found")

            validated_data["route"] = route
            assign_perm("view_route", user, route)
            assign_perm("delete_route", user, route)
            assign_perm("change_route", user, route)
            navigation_task = NavigationTask.create(**validated_data)
        return navigation_task


class ExternalNavigationTaskNestedTeamSerialiser(serializers.ModelSerializer):
    contestant_set = ContestantNestedTeamSerialiser(many=True)
    original_scorecard = SlugRelatedField(
        slug_field="shortcut_name",
        queryset=Scorecard.get_originals(),
        required=False,
        help_text="Reference to an existing scorecard name. This forms the basis for the values available in the 'scorecard' field. Currently existing scorecards: {}".format(
            # ", ".join(["'{}'".format(item) for item in Scorecard.get_originals()])
            ""
        ),
    )
    scorecard = ScorecardNestedSerialiser(required=False)
    route_file = serializers.CharField(write_only=True, required=True, help_text="Base64 encoded gpx file")
    internal_serialiser = ContestantNestedTeamSerialiser

    class Meta:
        model = NavigationTask
        exclude = ("route", "contest")

    def validate_route_file(self, value):
        if value:
            try:
                base64.decodebytes(bytes(value, "utf-8"))
            except Exception as e:
                raise ValidationError("route_file must be in a valid base64 string format.")
        return value

    def create(self, validated_data):
        # TODO: Add support for ANR track
        with transaction.atomic():
            contestant_set = validated_data.pop("contestant_set", [])
            route_file = validated_data.pop("route_file", None)
            try:
                route = create_precision_route_from_gpx(
                    base64.decodebytes(route_file.encode("utf-8")),
                    validated_data["original_scorecard"].use_procedure_turns,
                )
            except Exception as e:
                raise ValidationError("Failed building route from provided GPX: {}".format(e))
            user = self.context["request"].user
            try:
                validated_data["contest"] = self.context["contest"]
            except KeyError:
                raise Http404("Contest not found")

            validated_data["route"] = route
            assign_perm("view_route", user, route)
            assign_perm("delete_route", user, route)
            assign_perm("change_route", user, route)
            navigation_task = NavigationTask.create(**validated_data)
            for contestant_data in contestant_set:
                if isinstance(contestant_data["team"], Team):
                    contestant_data["team"] = contestant_data["team"].pk

            contestant_serialiser = self.internal_serialiser(
                data=contestant_set, many=True, context={"navigation_task": navigation_task}
            )
            contestant_serialiser.is_valid()
        contestant_serialiser.save()
        return navigation_task


class ExternalNavigationTaskTeamIdSerialiser(ExternalNavigationTaskNestedTeamSerialiser):
    """
    Does not provide team data input, only team ID for each contestant.
    """

    contestant_set = ContestantSerialiser(many=True)
    internal_serialiser = ContestantSerialiser

    class Meta:
        model = NavigationTask
        exclude = ("route", "contest")


class TrackAnnotationSerialiser(serializers.ModelSerializer):
    class Meta:
        model = TrackAnnotation
        fields = "__all__"


class ScoreLogEntrySerialiser(serializers.ModelSerializer):
    class Meta:
        model = ScoreLogEntry
        fields = "__all__"


class GateCumulativeScoreSerialiser(serializers.ModelSerializer):
    class Meta:
        model = GateCumulativeScore
        fields = "__all__"


########## Results service ##########
########## Write data ##########


########## Fetch data #############
class TeamTestScoreSerialiser(serializers.ModelSerializer):
    class Meta:
        model = TeamTestScore
        fields = "__all__"


class TaskSummarySerialiser(serializers.ModelSerializer):
    class Meta:
        model = TaskSummary
        fields = "__all__"


class TaskTestNestedSerialiser(serializers.ModelSerializer):
    teamtestscore_set = TeamTestScoreSerialiser(many=True)
    navigation_task_link = serializers.CharField()

    class Meta:
        model = TaskTest
        fields = "__all__"


class TaskNestedSerialiser(serializers.ModelSerializer):
    tasktest_set = TaskTestNestedSerialiser(many=True)
    tasksummary_set = TaskSummarySerialiser(many=True)

    class Meta:
        model = Task
        fields = "__all__"


# Details entry
class ContestResultsDetailsSerialiser(CountryFieldMixin, serializers.ModelSerializer):
    contestsummary_set = ContestSummaryNestedSerialiser(many=True)
    task_set = TaskNestedSerialiser(many=True)
    time_zone = TimeZoneSerializerField(required=True)
    permission_change_contest = serializers.BooleanField(read_only=True)
    country = CountryField(required=False)

    class Meta:
        model = Contest
        fields = "__all__"


# Team summary entry
class TeamResultsSummarySerialiser(serializers.ModelSerializer):
    contestsummary_set = ContestSummaryNestedSerialiser(many=True)

    class Meta:
        model = Team
        fields = "__all__"


######################  write data #####################
class ContestSummaryWithoutReferenceSerialiser(serializers.ModelSerializer):
    contest = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = ContestSummary
        fields = "__all__"

    def create(self, validated_data):
        try:
            validated_data["contest"] = self.context["contest"]
        except KeyError:
            raise Http404("Contest not found")

        return ContestSummary.objects.create(**validated_data)


class TaskSummaryWithoutReferenceSerialiser(serializers.ModelSerializer):
    task = serializers.PrimaryKeyRelatedField(queryset=Task.objects.all())

    class Meta:
        model = TaskSummary
        fields = "__all__"


class TeamTestScoreWithoutReferenceSerialiser(serializers.ModelSerializer):
    task_test = serializers.PrimaryKeyRelatedField(queryset=TaskTest.objects.all(), required=True)

    class Meta:
        model = TeamTestScore
        fields = "__all__"


# TODO: Not used?
class TaskTestWithoutReferenceNestedSerialiser(serializers.ModelSerializer):
    teamtestscore_set = TeamTestScoreWithoutReferenceSerialiser(many=True)
    task = serializers.PrimaryKeyRelatedField(queryset=Task.objects.all())

    class Meta:
        model = TaskTest
        fields = "__all__"


class TaskWithoutReferenceNestedSerialiser(serializers.ModelSerializer):
    tasktest_set = TaskTestWithoutReferenceNestedSerialiser(many=True)
    tasksummary_set = TaskSummaryWithoutReferenceSerialiser(many=True)

    class Meta:
        model = Task
        exclude = ("contest",)

    def create(self, validated_data):
        task_test_data = validated_data.pop("tasktest_set", [])
        task_summary_data = validated_data.pop("tasksummary_set", [])
        try:
            validated_data["contest"] = self.context["contest"]
        except KeyError:
            raise Http404("Contest not found")

        task = Task.objects.create(**validated_data)
        for item in task_summary_data:
            item["task"] = task
            TaskSummary.objects.create(**item)
        for task_test_data in task_test_data:
            task_test_data["task"] = task
            team_test_score_data = task_test_data.pop("teamtestscore_set", [])
            task_test = TaskTest.objects.create(**task_test_data)
            for i in team_test_score_data:
                i["task_test"] = task_test
                TeamTestScore.objects.create(**i)
        return task


class TaskSerialiser(serializers.ModelSerializer):
    class Meta:
        model = Task
        fields = "__all__"


class TaskTestSerialiser(serializers.ModelSerializer):
    navigation_task_link = serializers.CharField(read_only=True)

    class Meta:
        model = TaskTest
        fields = "__all__"


class EditableRouteSerialiser(ObjectPermissionsAssignmentMixin, serializers.ModelSerializer):
    route = serializers.JSONField(
        help_text=f"""
This is a JSON field that contains the elements of the route. It is a list of objects, where each block has a type field that determines which of the fields are in the object.
[
    {{
        "feature_type": <feature_type>, 
        "layer_type": <layer_type>, 
        ...
        ...
    }}
]    
Feature type and layer type are required fields in the following combinations are valid:
feature_type, layer_type
track, polyline # The route itself. This is always required.
to, polyline # Optional take-off gate
ldg, polyline # Optional landing gate
prohibited, polygon # Optional prohibited zone
penalty, polygon # Optional penalty zone
info, polygon # Optional information zone
gate, polygon # Optional gate, used for poker runs

Each line in the list above represents a route object type. Now follows a detailed description of each object type.

Track:
{{
    "feature_type": "track",
    "layer_type": "polyline", 
    "name": "Track", # Can be anything, but is not used anywhere
    "tooltip_position": [0, 0], Relative location of name, not relevant for track
    "track_points": [ # A list of all the waypoints in the route
        {{
            "name": "SP", # Any waypoint name you like
            "gateType": "tp", # The type of gate.The first must be of type "{STARTINGPOINT}" in the last must be of type "{FINISHPOINT}". Other valid values are {GATE_TYPES}
            "timeCheck": true, # Whether the gate is considered for time penalty
            "gateWidth": 1, # The width of the gate, NM
            "position": {{"lat": 60, "lng": 11}} # The position of the gate, must match what is at the same list index in the geojson  
        }}, 
        {{}}
    ], 
    "geojson": {{ # A geojson object that describes the line of the track
        "type": "Feature",
        "properties": {{}},
        "geometry": {{
            "type": "LineString",
            "coordinates": [[lng, lat] for item in track_points], # The list of coordinates similar to what is in track_points. Note the longitude, latitude order
        }},
    }},
}}

Takeoff and landing gate:
{{
    "name": "TO",
    "layer_type": "polyline",
    "track_points": [], # Leave empty
    "feature_type": "to", # to or ldg
    "tooltip_position": [0, 0],
    "geojson": {{
        "type": "Feature",
        "properties": {{}},
        "geometry": {{
            "type": "LineString", 
            "coordinates": [[lng, lat], [lng, lat]] # List of two (longitude, latitude) pairs.
        }},
    }},
}}

Prohibited, penalty, information, gate zones
{{
    "name": "Prohibited 1",
    "layer_type": "polygon",
    "track_points": [], # Leave empty
    "feature_type": "prohibited", # prohibited, penalty, info, gate
    "tooltip_position": [0, 0], # Useful if the polygon name overlaps anything in the map. The numbers of pixels from centre of object
    "geojson": {{
        "type": "Feature",
        "properties": {{}},
        "geometry": {{
            "type": "Polygon",
            "coordinates": [positions],  # Apparently a list of list of positions, i.e. multiple polygons. Should be lat, lon
        }},
    }},
}}

    """
    )
    editors = UserSerialiser(many=True, read_only=True)
    is_editor = serializers.SerializerMethodField("get_is_editor", read_only=True)
    number_of_waypoints = serializers.IntegerField(read_only=True)
    route_length = serializers.FloatField(read_only=True)

    class Meta:
        model = EditableRoute
        exclude = ("route_type",)

    def get_permissions_map(self, created):
        user = self.context["request"].user
        return {"change_editableroute": [user], "delete_editableroute": [user], "view_editableroute": [user]}

    def get_is_editor(self, editable_route):
        return "change_editableroute" in get_user_perms(self.context["request"].user, editable_route)


serializers.SerializerMethodField("get_is_editor", read_only=True)
