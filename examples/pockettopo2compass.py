#!/usr/bin/env python2
"""
This simple example file illustrates a conversion from PocketTopo .TXT survey to a Compass .DAT
survey. The general idea is simple, but "the devil is in the details"; Davies doesn't gloss over
the differences in each format, so we must carefully convert from one shot format to the other, as
well has "hack" a few nuances.


usage: pockettopo2compass.py [-h] [--no-splays] [--lrud] TXTFILE [TXTFILE...]

Create Compass .DAT files from PocketTopo .TXT export files.

positional arguments:
  FNAME        One or more PocketTopo .TXT export files

optional arguments:
  -h, --help   show this help message and exit
  --no-splays  Exclude splay shots from output
  --lrud       Calculate LRUD values from splay shots
"""

import sys
import os, os.path
import logging
import random

from davies import compass
from davies import pockettopo
from davies.survey_math import hd, vd, angle_delta, cartesian_offset, m2f


LRUD_DELTA = 60  # cone theta within which a splay will be considered a potential LRUD


def find_candidate_splays(splays, azm, inc, delta=LRUD_DELTA):
    """Given a list of splay shots, find candidate LEFT or RIGHT given target AZM and INC"""
    return [splay for splay in splays
            if
            angle_delta(splay['AZM'], azm) <= delta/2
            and
            angle_delta(splay['INC'], inc) <= delta/2
            ]

def find_candidate_vert_splays(splays, inc, delta=LRUD_DELTA):
    """Given a list of splay shots, find candidate UP or DOWN given target INC (90, or -90)"""
    # FIXME: perhaps this is the wrong approach. Should we simply select max of anything with negative/positive INC for DOWN/UP?
    #return [splay for splay in splays if angle_delta(splay['INC'], inc) <= delta/2]
    if inc == 90:
        return [splay for splay in splays if splay['INC'] >= 30]
    elif inc == -90:
        return [splay for splay in splays if splay['INC'] <= -30]


def shot2shot(insurvey, inshot, calculate_lrud=True):
    """Convert a PocketTopo `Shot` to a Compass `Shot`"""
    # FIXME: requires angles in degrees only, no grads

    splays = insurvey.splays[inshot['FROM']]
    if calculate_lrud and not inshot.is_splay and splays:
        # Try our best to convert PocketTopo splay shots into LRUDs
        print '\n\n' 'sta %s has %d splays' % (inshot['FROM'], len(splays))

        left_azm, right_azm = (inshot['AZM'] - 90) % 360, (inshot['AZM'] + 90) % 360
        left_shot, right_shot = None, None
        left_candidates = find_candidate_splays(splays, left_azm, 0)
        if left_candidates:
            left_shot = max(left_candidates, key=lambda shot: hd(shot['INC'], shot['LENGTH']))
            left = hd(left_shot['INC'], left_shot['LENGTH'])
        else:
            left = 0
        right_candidates = find_candidate_splays(splays, right_azm, 0)
        if right_candidates:
            right_shot = max(right_candidates, key=lambda shot: hd(shot['INC'], shot['LENGTH']))
            right = hd(right_shot['INC'], right_shot['LENGTH'])
        else:
            right = 0

        print '\t' 'left=%.1f  azm=%.1f  right=%.1f' % (left_azm, inshot['AZM'], right_azm)
        print '\t' '%d candidate LEFT shots' % len(left_candidates)
        for splay in left_candidates:
            print '\t\t\t' + str(splay)
        print '\t\t' '%.1f - Chose: %s' % (left, str(left_shot))
        print '\t' '%d candidate RIGHT shots' % len(right_candidates)
        for splay in right_candidates:
            print '\t\t\t' + str(splay)
        print '\t\t' '%.1f - Chose: %s' % (right, str(right_shot))

        up_candidates = find_candidate_vert_splays(splays, 90)
        if up_candidates:
            up_shot = max(up_candidates, key=lambda splay: vd(splay['INC'], splay['LENGTH']))
            up = vd(up_shot['INC'], up_shot['LENGTH'])
        else:
            up = 0
        down_candidates = find_candidate_vert_splays(splays, -90)
        if down_candidates:
            down_shot = max(down_candidates, key=lambda splay: vd(splay['INC'], splay['LENGTH']))  # TODO: should vd() give negative and we find min()?
            down = vd(down_shot['INC'], down_shot['LENGTH'])
        else:
            down = 0

        print '\t', inshot, 'LRUD=', ', '.join(('%0.1f' % v) for v in (left, right, up, down))
        assert(all(v >=0 for v in (left, right, up, down)))
    else:
        up, down, left, right = None, None, None, None

    return compass.Shot([
        ('FROM', inshot['FROM']),
        # Compass requires a named TO station, so we must invent one for splays
        ('TO', inshot['TO'] or '%s.s%03d' % (inshot['FROM'], random.randint(0,1000))),
        ('LENGTH', m2ft(inshot.length)),
        # BEARING/AZM named inconsistently in Davies to reflect each program's arbitrary name. We
        # can't use `inshot.azm` here because we need the "raw" compass value without declination
        ('BEARING', inshot['AZM']),
        ('INC', inshot.inc),
        ('LEFT', m2ft(left) if left is not None else -9.90),
        ('UP', m2ft(up) if left is not None else -9.90),
        ('DOWN', m2ft(down) if left is not None else -9.90),
        ('RIGHT', m2ft(right) if left is not None else -9.90),  # Compass requires this order!
        # Compass 'L' flag excludes splays from cave length calculation
        ('FLAGS', (compass.Exclude.LENGTH, compass.Exclude.PLOT) if inshot.is_splay else ()),
        # COMMENTS/COMMENT named inconsistently in Davies to reflect each program's arbitrary name
        ('COMMENTS', inshot['COMMENT'])
    ])


def pockettopo2compass(txtfilename, exclude_splays=False, calculate_lrud=False):
    """Main function which converts a PocketTopo .TXT file to a Compass .DAT file"""
    print 'Converting PocketTopo data file %s ...' % txtfilename

    # Read our PocketTopo .TXT file, averaging triple-shots in the process
    infile = pockettopo.TxtFile.read(txtfilename, merge_duplicate_shots=True)

    cave_name = os.path.basename(txtfilename).rsplit('.', 1)[0].replace('_', ' ')
    outfilename = txtfilename.rsplit('.', 1)[0] + '.DAT'

    # Create the Compass DatFile in memory and start building, we'll write to file when done
    outfile = compass.DatFile(cave_name)

    for insurvey in infile:
        # Our Compass and PocketTopo Surveys are very similar...
        outsurvey = compass.Survey(
            insurvey.name, insurvey.date, comment=insurvey.comment, cave_name=cave_name, declination=insurvey.declination
        )
        outsurvey.length_units = 'M'
        outsurvey.passage_units = 'M'
        
        for inshot in insurvey:
            if inshot.is_splay and exclude_splays:
                continue  # skip

            # ...but the Shot data fields require some tweaking, see `shot2shot()` for details
            outshot = shot2shot(insurvey, inshot, calculate_lrud)
            outsurvey.add_shot(outshot)

        outfile.add_survey(outsurvey)

        # DEBUG
        #for station, splays in list(insurvey.splays.items()):
        #    print 'sta %s has %d splays' % (station, len(splays))

    # Finally all built, dump the whole DatFile/Surveys/Shots structure to file
    outfile.write(outfilename)
    print 'Wrote Compass data file %s .' % outfilename


if __name__ == '__main__':
    import argparse

    logging.basicConfig(level=logging.DEBUG)

    parser = argparse.ArgumentParser(description='Create Compass .DAT files from PocketTopo .TXT export files.')
    parser.add_argument('filenames', metavar='FNAME', nargs='+', help='One or more PocketTopo .TXT export files')
    parser.add_argument('--no-splays', dest='exclude_splays', action='store_true', help='Exclude splay shots from output')
    parser.add_argument('--lrud', dest='calculate_lrud', action='store_true', help='Calculate LRUD values from splay shots')
    args = parser.parse_args()

    for txtfilename in args.filenames:
        pockettopo2compass(txtfilename, args.exclude_splays, args.calculate_lrud)
