"""
davies.compass: Module for parsing and working with Compass source files
"""

import os.path
import logging
import datetime
import codecs
from collections import OrderedDict

log = logging.getLogger(__name__)


# Compass OO Model


class Shot(OrderedDict):
    """Representation of a single shot in a Compass Survey."""

    def __init__(self, *args, **kwargs):
        self.declination = kwargs.pop('declination', 0.0)
        OrderedDict.__init__(self, *args, **kwargs)

    @property
    def azm(self):
        """Corrected azimuth, taking into account backsight, declination, and compass corrections."""
        azm1 = self.get('BEARING', None)
        azm2 = self.get('AZM2', None)
        if azm1 is None and azm2 is None:
            return None
        if azm2 is None:
            return azm1 + self.declination
        if azm1 is None:
            return (azm2 + 180) % 360 + self.declination
        return (azm1 + (azm2 + 180) % 360) / 2.0 + self.declination

    @property
    def inc(self):
        """Corrected inclination, taking into account backsight and clino corrections."""
        inc1 = self.get('INC', None)
        inc2 = self.get('INC2', None)
        if inc1 is None and inc2 is None:
            return None
        if inc2 is None:
            return inc1
        if inc1 is None:
            return -1 * inc2
        return (inc1 - inc2) / 2.0

    @property
    def length(self):
        """Corrected distance, taking into account tape correction."""
        return self.get('LENGTH', None)


class Survey(object):
    """Representation of a Compass Survey object. A Survey is a container for :class:`Shot` objects."""

    def __init__(self, name=None, date=None, comment=None, team=None, declination=None, lrud_format=None, corrections=None, cave_name=None, shot_header=None, shots=None):
        self.name = name
        self.date = date
        self.comment = comment
        self.team = team
        self.declination = declination
        self.lrud_format = lrud_format
        self.corrections = corrections
        self.cave_name = cave_name
        self.shot_header = shot_header
        self.shots = shots if shots else []

    def add_shot(self, shot):
        """Add a shot dictionary to :attr:`shots`."""
        self.shots.append(shot)

    @property
    def length(self):
        """Total surveyed length."""
        return sum([shot.length for shot in self.shots])

    def __len__(self):
        return len(self.shots)

    def __iter__(self):
        for shot in self.shots:
            yield shot

    def __contains__(self, item):
        for shot in self.shots:
            if item in (shot.get('FROM', None), shot.get('TO', None)):
                return True
        return False


class DatFile(object):
    """
    Representation of a Compass .DAT File. A DatFile is a container for :class:`Survey` objects.

    :ivar name:    (string) the DatFile's "name", not necessarily related to its filename
    :ivar surveys: (list of :class:`Survey`)
    """

    def __init__(self, name=None):
        self.name = name
        self.surveys = []

    def add_survey(self, survey):
        """Add a :class:`Survey` to :attr:`surveys`."""
        self.surveys.append(survey)

    @property
    def length(self):
        """Total surveyed length."""
        return sum([survey.length for survey in self.surveys])

    def __len__(self):
        return len(self.surveys)

    def __iter__(self):
        for survey in self.surveys:
            yield survey

    def __contains__(self, item):
        for survey in self.surveys:
            if item == survey.name or item == survey:
                return True
        return False

    def __getitem__(self, item):
        for survey in self.surveys:
            if item == survey.name or item == survey:
                return survey
        raise KeyError(item)


class UTMLocation(object):
    """Represents a UTM-based coordinate for fixed stations."""

    def __init__(self, easting, northing, elevation, zone=None, convergence=None, datum=None):
        self.easting = easting
        self.northing = northing
        self.elevation = elevation
        self.zone = int(zone) if zone is not None else None
        self.convergence = convergence
        self.datum = datum

    def __str__(self):
        return "<%s UTM Zone %s %0.1fE %0.1fN %0.1f>" % (self.datum, self.zone, self.easting, self.northing, self.elevation)


class Project(object):
    """
    Representation of a Compass .MAK Project file. A Project is a container for :class:`DatFile` objects.

    :ivar name:         (string)
    :ivar linked_files: (list of :class:`DatFile`)
    """

    def __init__(self, name=None, base_location=None, file_params=None):
        self.name = name
        self.base_location = base_location
        self.file_params = file_params
        self.linked_files = []

    def add_linked_file(self, datfile):
        """Add a :class:`DatFile` to :attr:`linked_files`."""
        self.linked_files.append(datfile)

    def __len__(self):
        return len(self.linked_files)

    def __iter__(self):
        for linked_file in self.linked_files:
            yield linked_file

    def __getitem__(self, item):
        for datfile in self.linked_files:
            if item == datfile.name or item == datfile:
                return datfile
        raise KeyError(item)


# File Parsing Utilities


def name_from_filename(fname):
    return os.path.splitext(os.path.basename(fname))[0].replace('_', ' ')


class ParseException(Exception):
    """Exception raised when parsing fails."""
    pass


class CompassSurveyParser(object):
    """Parser for a Compass survey string."""

    def __init__(self, survey_str):
        """:param survey_str: string multiline representation of survey as found in .DAT file"""
        self.survey_str = survey_str

    _FLOAT_KEYS = ['LENGTH', 'BEARING', 'AZM2', 'INC', 'INC2', 'LEFT', 'RIGHT', 'UP', 'DOWN']
    _INF_KEYS = ['LEFT', 'RIGHT', 'UP', 'DOWN']

    @staticmethod
    def _coerce(key, val):
        if val == '-999.00':  # no data
            return None

        if key in CompassSurveyParser._INF_KEYS and val in ('-9.90', '-9999.00'):  # passage
            return float('inf')

        if key in CompassSurveyParser._FLOAT_KEYS:
            try:
                return float(val)
            except TypeError as e:
                log.warn("Unable to coerce to float %s=%s (%s)", key, val, type(val))

        return val

    @staticmethod
    def _parse_date(datestr):
        datestr = datestr.strip()
        for fmt in ['%m %d %Y', '%m %d %y']:
            try:
                return datetime.datetime.strptime(datestr, fmt).date()
            except ValueError:
                pass
        raise ParseException("Unable to parse SURVEY DATE: %s" % datestr)

    def parse(self):
        """Parse our string and return a Survey object, None, or raise :exc:`ParseException`"""
        if not self.survey_str:
            return None
        lines = self.survey_str.splitlines()
        if len(lines) < 10:
            raise ParseException("Expected at least 10 lines in a Compass Survey, only found %d!\nlines=%s" % (len(lines), lines))

        # undelimited Cave Name may be empty string and "skipped"
        first_line = lines.pop(0).strip()
        if first_line.startswith('SURVEY NAME:'):
            cave_name = ''
            name = first_line.strip('SURVEY NAME:').strip()
        else:
            cave_name = first_line
            name = lines.pop(0).split('SURVEY NAME:', 1)[1].strip()

        # Date and Comment on one line, Comment may be missing
        date_comment_toks = lines.pop(0).split('SURVEY DATE:', 1)[1].split('COMMENT:')
        date = CompassSurveyParser._parse_date(date_comment_toks[0])
        comment = date_comment_toks[1].strip() if len(date_comment_toks) > 1 else ''

        lines.pop(0)  # SURVEY TEAM:\n
        team = [codecs.decode(member.strip(), 'utf-8', 'replace') for member in lines.pop(0).split(',')]  # FIXME: ASCII file but found 'Tanya Pietra\xdf'

        dec_fmt_corr = lines.pop(0)  # TODO: implement declination, format, instrument correction(s)

        lines.pop(0)
        shot_header = lines.pop(0).split()
        lines.pop(0)

        survey = Survey(name=name, date=date, comment=comment, team=team, cave_name=cave_name, shot_header=shot_header)

        # TODO: for now, let's totally ignore flags and comments
        shots = []
        shot_lines = lines
        for shot_line in shot_lines:
            shot_vals = shot_line.split(None, len(shot_header) - 2)  # last two columns are FLAGS and COMMENTS, either one may be missing

            if len(shot_vals) > len(shot_header) - 2:
                flags_comment = shot_vals.pop()
                if not flags_comment.startswith('#|'):
                    flags, comment = None, flags_comment
                else:
                    flags, comment = flags_comment.split('#|', 1)[1].split('#', 1)
                shot_vals += [flags, comment.strip()]

            shot = Shot([(header, self._coerce(header, val)) for (header, val) in zip(shot_header, shot_vals)])
            survey.add_shot(shot)

        log.debug("Survey: name=%s shots=%d length=%0.1f date=%s team=%s\n%s", name, len(shots), survey.length, date, team, '\n'.join([str(shot) for shot in survey.shots]))

        return survey


class CompassDatParser(object):
    """Parser for Compass .DAT data files"""

    def __init__(self, datfilename):
        """:param datfilename: (string) filename"""
        self.datfilename = datfilename

    def parse(self):
        """Parse our data file and return a :class:`DatFile` or raise :exc:`ParseException`."""
        log.debug("Parsing Compass .DAT file %s ...", self.datfilename)
        datobj = DatFile(name_from_filename(self.datfilename))

        with open(self.datfilename, 'rb') as datfile:
            full_contents = datfile.read()
            survey_strs = [survey_str.strip() for survey_str in full_contents.split('\x0C')]

            if survey_strs[-1] == '\x1A':
                survey_strs.pop()  # Compass may place a "soft EOF" with ASCII SUB char

            log.debug("Parsed %d raw surveys from Compass .DAT file %s.", len(survey_strs), self.datfilename)
            for survey_str in survey_strs:
                if not survey_str:
                    continue
                survey = CompassSurveyParser(survey_str).parse()
                datobj.add_survey(survey)

        return datobj


class CompassProjectParser(object):
    """Parser for Compass .MAK project files."""

    def __init__(self, projectfile):
        """:param projectfile: (string) filename"""
        self.makfilename = projectfile

    def parse(self):
        """Parse our project file and return :class:`Project` object or raise :exc:`ParseException`."""
        log.debug("Parsing Compass .MAK file %s ...", self.makfilename)

        base_location = None
        linked_files = []
        file_params = set()

        def parse_linked_file(value):
            log.debug("Parsing linked file:  %s", value)
            value = value.rstrip(';')
            toks = value.split(',', 1)
            if len(toks) == 1:
                return toks[0]
            else:
                return toks[0]  # TODO: implement link stations and fixed stations

        with open(self.makfilename, 'rb') as makfile:
            prev = None

            for line in makfile:
                line = line.strip()

                if not line:
                    continue

                header, value = line[0], line[1:]

                if prev:
                    if line.endswith(';'):
                        linked_file = parse_linked_file(prev + line.rstrip(';'))
                        linked_files.append(linked_file)
                        prev = None
                    else:
                        prev += value
                    continue

                if header == '/':
                    pass  # comment

                elif header == '@':
                    value = value.rstrip(';')
                    base_location = UTMLocation(*(float(v) for v in value.split(',')))

                elif header == '&':
                    value = value.rstrip(';')
                    base_location.datum = value

                elif header == '%':
                    value = value.rstrip(';')
                    base_location.convergence = float(value)

                elif header == '!':
                    value = value.rstrip(';')
                    file_params = set(value.upper())

                elif header == '#':
                    if value.endswith(';'):
                        linked_files.append(parse_linked_file(value))
                        prev = None
                    else:
                        prev = value

            log.debug("Project:  base_loc=%s  params=%s  linked_files=%s", base_location, file_params, linked_files)

            project = Project(name_from_filename(self.makfilename), base_location, file_params)

            for linked_file in linked_files:
                # TODO: we need to support case-insensitive path resolution on case-sensitive filesystems
                linked_file_path = os.path.join(os.path.dirname(self.makfilename), os.path.normpath(linked_file.replace('\\', '/')))
                datfile = CompassDatParser(linked_file_path).parse()
                project.add_linked_file(datfile)

            return project
