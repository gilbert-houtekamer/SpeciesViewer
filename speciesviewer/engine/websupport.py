import os
import html
import re
import posixpath
import shutil
import subprocess
from utils import no_window
import time
import tempfile
import math
from datetime import datetime
from operator import itemgetter
from collections import defaultdict
import logging
from pathlib import Path
from PIL import Image

global config

from engine import COLUMN_NAMES, SELECTED_FIELDS
from engine.specieslib import Level
import engine.projectlib
import engine.breadcrumb
from utils.exiftool_path import exe as exiftool_exe
from utils.atomic_replace import copy_with_retry

logger = logging.getLogger("speciesviewer")

ClassificationGroupName = ["Species","Family", "Order", "Class", "Phylum", "Kingdom", "Biota"]
ClassificationGroupNameNL = ["Soort", "Familie", "Orde", "Klasse","Stam", "Rijk", "Biota"]
ClassificationGroupLidwoord = ["de", "de", "de", "de","de", "het", "de"]
ClassificationGroupNameNLmeervoud = ["Soorten", "Families", "Orden", "Klassen","Stammen", "Rijken", "Biota"]
ClassificationGroupNameENmeervoud = ["Species","Families", "Orders", "Classes", "Phyla", "Kingdoms", "Biota"]

def _src_already_at_final(MyProject, src, target_path, basename):
    """True when `src` is the same file as the spot sv7 would copy it
    to in the repository's biota tree. Used to skip the redundant
    pass through frozen for files that are already at home (typical
    for historical pictures in master-mode runs)."""
    try:
        final_dst = os.path.join(MyProject.getRepositoryPath(),
                                  target_path, basename)
        if not (os.path.exists(src) and os.path.exists(final_dst)):
            return False
        return os.path.samefile(src, final_dst)
    except OSError:
        return False


# web support
# -- subspecies on the website (ch.6 subspecies step 2, 2026-10-18) ----------
# A subspecies is a row beside its species with SubspeciesOf. When its species
# has a page, the subspecies is shown THERE (its pictures among the species'
# own, and in a Subspecies block) instead of as a tile of its own.
def _subspecies_of(MyProject, name):
    rows = getattr(getattr(MyProject, "sc", None), "sc_dict", None) or {}
    return (rows.get(name) or {}).get("SubspeciesOf", "") or ""


def _folded_subspecies(MyProject, name):
    """True when `name` is a subspecies shown on its species' page."""
    parent = _subspecies_of(MyProject, name)
    node = MyProject.sp.species_dict.get(parent) if parent else None
    return node is not None and MyProject.hasWebPage(node)


def _subspecies_with_pages(MyProject, species):
    """The species' subspecies that have pictures here, in list order."""
    name = species["ScientificName"]
    family = MyProject.sp.species_dict.get(species.get("Up", ""), {})
    return [MyProject.sp.species_dict[leaf]
            for leaf in MyProject.orderedLeaves(family.get("Leaves", []))
            if leaf in MyProject.sp.species_dict and _subspecies_of(MyProject, leaf) == name
            and MyProject.hasWebPage(MyProject.sp.species_dict[leaf])]


# A template line that is only a link to a group page: a menu item
# (<a … class="w3-bar-item …">Birds</a>) or a list item (<li><a …>Birds</a></li>).
_GROUP_LINK = re.compile(r'^\s*(?:<li>)?<a href="%%\d+%%biota/(?:[^"/]+/)*([^"/]+)-(?:en|nl)\.html"[^>]*>[^<]*</a>(?:</li>)?\s*$')


def _group_has_pictures(MyProject, group):
    """True when this repository has pictures with a JPG in `group` (what its
    page would show), or when that cannot be told (no species tree yet).
    A blog page holds one day's tree; the blog step then sets menu_groups,
    the whole album's groups (batch/album_source.album_groups)."""
    menu_groups = getattr(MyProject, "menu_groups", None)
    if menu_groups is not None:
        return group in menu_groups
    sp = getattr(MyProject, "sp", None)
    if sp is None or "biota" not in sp.species_dict:
        return True
    g = sp.species_dict.get(group)
    if g is None:
        return False
    try:
        return int(g.get("iPicturesWithJPGsCollection") or 0) + int(g.get("iPicturesWithJPGsThisRun") or 0) > 0
    except ValueError:
        return True


def getTemplateOutputString(MyProject, species, file, picture, TemplateLine):
    # ONE menu for every repository: leave out the groups this one has no
    # pictures of — an album without fungi linked an old or empty Fungi page.
    m = _GROUP_LINK.match(TemplateLine)
    if m and not _group_has_pictures(MyProject, m.group(1)):
        return ""
# 	Parse line in pieces between tokens (%%n%%) with n an integer that refers to a species-related blurp.
# 	The syntax implies that text and token %% seperators alternate, which we keep track off with TextOrToken 
# 	probably want nested call for subroutines, ie make this an output string.
# 	TemplateOutputString = ""
    try:
        outputLine = ""
        tokens = TemplateLine.split('%%')
        if len(tokens) == 1:
            # no tokens, return line as-is
            return TemplateLine
        else:
            # text on even positions (0, 2, 4, ..) tokens on odd position
            OddPosition = 0
            for token in tokens:
                if not OddPosition:         # not a token
                    outputLine += token
                else:                       # handle token
                    try:
                        outputLine += formatToken(int(token), MyProject, species, file, picture)       # '<token ' + token + '>'
                    except Exception as e:
                        outputLine += '<Failed to handle token ' + token + '>'
                        who = "Blog index" if species is None else species['ScientificName']
                        logger.warning(f"{who}: failed token {token}: {e}")
                OddPosition = 1 - OddPosition
            return outputLine
    except Exception as e:
        logger.warning(f"{TemplateLine} failed: {e}")
        outputLine = TemplateLine
    return tokens

def getFormattedCityStateCountry(picture):
	# Join only the non-empty fields so missing components don't leave
	# stray separators — e.g. ('', 'Arusha', 'Tanzania') used to render
	# as ", Arusha, Tanzania" with a leading comma.
	# Municipality is the larger entity, so it sits between City and State; it is
	# dropped when it equals City or is empty (then City stands alone in its slot,
	# or Municipality takes City's slot when City itself is empty). .get() keeps
	# older picture rows (read from a pre-Municipality CSV) working.
	city = picture.get('City', '') or ''
	muni = picture.get('Municipality', '') or ''
	parts = [city]
	if muni and muni != city:
		parts.append(muni)
	parts += [picture.get('State', '') or '', picture.get('Country', '') or '']
	return ", ".join(p for p in parts if p)

def isUnnamedRoad(locationname):
    if locationname in ['Vía Sin Nombre', 'Route sans nom', 'Unnamed Road']:   # , 'Onbekende weg' ?
        return True
    return False

OBSCURE_FAMILIES = {"orchidaceae"}

def getFamilyForSpecies(MyProject, species):
    """Walk Up from a SPECIES-level entry to its family dict. Returns {} if not found."""
    if not species or species.get('Level') != Level.SPECIES:
        return {}
    up = MyProject.getSpecies(species.get('Up', ''))
    if up and up.get('Level') == Level.FAMILY:
        return up
    return {}

def isObscure(MyProject, species):
    family = getFamilyForSpecies(MyProject, species)
    return family.get('ScientificName', '').lower() in OBSCURE_FAMILIES

def _locationParts(picture, obscure=False):
    """(place, bracketed) — the text before the brackets and the text inside them
    ('' = no brackets), so getFormattedLocation can add the altitude INSIDE an
    existing bracket instead of opening a second one."""
    if obscure:
        return getFormattedCityStateCountry(picture), ""
    if picture['Location'] == "" or isUnnamedRoad(picture['Location']):
        return getFormattedCityStateCountry(picture), ""
    else:
        if picture['Location'] != "" and len(picture['Location']) > 15:
            return picture['Location'], ""
        if picture['Country'] == "": return picture['Location'], ""
        if picture['Location'] == picture['City'] or picture['Location'] == picture['Country'] or isUnnamedRoad(picture['Location']):
            return getFormattedCityStateCountry(picture), ""
        if picture['City'] == '':
            picture['City'] = picture['Location']
            return getFormattedCityStateCountry(picture), ""
        return picture['Location'], getFormattedCityStateCountry(picture)

def getFormattedLocation(picture, obscure=False, altitude=False):
    """The place as the website writes it: 'Jardim Meudon (Teresópolis, Rio de
    Janeiro, Brazil)'. With altitude=True the height joins it — inside the bracket
    when there is one ('…, Brazil, 1014 m)'), else as its own '(1014 m)'. Never for
    an obscure species: a height narrows a hidden location down again. No place
    text → no altitude either (a bare height says nothing on its own)."""
    from engine.geo import altitude_text
    place, inner = _locationParts(picture, obscure)
    alt = altitude_text(picture.get('GPSAltitude', '')) if (altitude and not obscure and place) else ""
    if alt:
        inner = f"{inner}, {alt}" if inner else alt
    return f"{place} ({inner})" if inner else place

def makeLinkScienticficName(MyProject, BiotaWebRoot, classification, workstr):
    if classification['iSpeciesCollectionCount'] == 0:
        workstr +=  classification['ScientificName'].strip('0123456789 ').capitalize()
    else:
        workstr +=  '<a href="' + BiotaWebRoot + classification["TargetPath"] + classification["ScientificName"] + '-' + MyProject.getLanguage() +  '.html\"' + '>\n'
        workstr += '			         ' + classification['ScientificName'].strip('0123456789 ').capitalize() + '</a>'
    return workstr

def makeLinkSpeciesDisplay(MyProject, BiotaWebRoot, species, display_html):
    """Wrap an already-formatted HTML fragment (e.g. '<i>Saxicola rubicola</i>')
    in a link to the species page within the current trip, if that page exists."""
    if int(species.get('iSpeciesCollectionCount', 0)) == 0:
        return display_html
    url = BiotaWebRoot + species["TargetPath"] + species["ScientificName"] + '-' + MyProject.getLanguage() + '.html'
    return '<a href="' + url + '">' + display_html + '</a>'


def getBlogNeighbours(MyProject):
    """Return (prev_blogday, next_blogday) for the currently-rendered blog.
    Honours Blog.committed_blogdays when it is set (e.g. by RedoBlog) so only
    committed days are considered. During the initial blog write the current
    blogday is not yet in blog_dict — treat it as the newest and use the
    latest known blog as Previous."""
    current = MyProject.Blog.blogday
    committed = getattr(MyProject.Blog, 'committed_blogdays', None)
    if committed:
        ordered = sorted(bd for bd in MyProject.Blog.blog_dict if bd in committed)
    else:
        ordered = sorted(MyProject.Blog.blog_dict.keys())
    if current in ordered:
        idx = ordered.index(current)
        prev_blog = ordered[idx - 1] if idx > 0 else None
        next_blog = ordered[idx + 1] if idx < len(ordered) - 1 else None
    elif ordered:
        prev_blog = ordered[-1]
        next_blog = None
    else:
        prev_blog = None
        next_blog = None
    return prev_blog, next_blog


def buildBlogNavSentence(MyProject):
    """Closing sentence of a blog page with links to Top, Previous Blog and
    Next Blog."""
    language = MyProject.getLanguage()
    biotablog_url = MyProject.getBiotaWebRoot() + 'biotablog/'
    index_link = '<a href="' + biotablog_url + 'blog index-' + language + '.html">Top</a>'
    prev_blog, next_blog = getBlogNeighbours(MyProject)

    def nav_link(blogday, label):
        return '<a href="' + blogday + '-' + language + '.html">' + label + '</a>'

    if language == 'en':
        parts = ['The link ' + index_link + ' will take you back to the top of this blog list']
        if prev_blog:
            parts.append(nav_link(prev_blog, 'Previous Blog') + ' to the previous day')
        if next_blog:
            parts.append(nav_link(next_blog, 'Next Blog') + ' to the next blog')
        if len(parts) == 1:
            return parts[0] + '.'
        if len(parts) == 2:
            return parts[0] + ' and ' + parts[1] + '.'
        return parts[0] + ', ' + parts[1] + ' and ' + parts[2] + '.'
    else:
        parts = ['Met de link ' + index_link + ' kom je terug bovenaan de blog lijst']
        if prev_blog:
            parts.append(nav_link(prev_blog, 'Vorige Blog') + ' voor de vorige dag')
        if next_blog:
            parts.append(nav_link(next_blog, 'Volgende Blog') + ' voor de volgende')
        if len(parts) == 1:
            return parts[0] + '.'
        if len(parts) == 2:
            return parts[0] + ' en ' + parts[1] + '.'
        return parts[0] + ', ' + parts[1] + ' en ' + parts[2] + '.'


def buildSliderNavSentence(MyProject):
    """Centered navigation line for slider pages: Previous / Next only,
    linking to the corresponding slider pages of committed neighbour days."""
    language = MyProject.getLanguage()
    prev_blog, next_blog = getBlogNeighbours(MyProject)

    def nav_link(blogday, label):
        return '<a href="' + blogday + '-slider-' + language + '.html">' + label + '</a>'

    if language == 'en':
        if prev_blog and next_blog:
            return nav_link(prev_blog, 'Previous Blog') + ' &nbsp;|&nbsp; ' + nav_link(next_blog, 'Next Blog')
        if prev_blog:
            return nav_link(prev_blog, 'Previous Blog')
        if next_blog:
            return nav_link(next_blog, 'Next Blog')
        return ''
    else:
        if prev_blog and next_blog:
            return nav_link(prev_blog, 'Vorige Blog') + ' &nbsp;|&nbsp; ' + nav_link(next_blog, 'Volgende Blog')
        if prev_blog:
            return nav_link(prev_blog, 'Vorige Blog')
        if next_blog:
            return nav_link(next_blog, 'Volgende Blog')
        return ''

# new csv files will have sexe
def formatSexe(upn):
    try:
        if upn['malefemale'] == 'male' or upn['malefemale'] == 'man':
            return ' ♂'
        elif upn['malefemale'] == 'female' or upn['malefemale'] == 'vrouw':
            return ' ♀'
        elif upn['malefemale'] == 'couple' or upn['malefemale'] == 'paar' or upn['malefemale'] == 'paartje':
            return ' ♂♀'
    except:
        pass
    return ''

def formatAgePlumage(upn):
    workstr = ''
    try:
        if upn['ageintext'] != '' or upn['summerwinter'] != '':
            workstr = ' ('
            if upn['ageintext'] != '': 
                workstr += upn['ageintext']
                if upn['summerwinter'] != '':   # unlikely combination
                    workstr += ', ' + upn['summerwinter']
            elif upn['summerwinter'] != '':
                workstr += upn['summerwinter']
            workstr += ')'
    except:
        pass 
    
    return workstr

def formatSubspecies(name):
    """Format a 3-word scientific name with 'ssp.' before the subspecific epithet."""
    parts = name.split()
    if len(parts) == 3:
        return f"{parts[0]} {parts[1]} ssp. {parts[2]}"
    return name

# try different formats
def tryJPGtime(rawtime, MyProject):
    try:
        if rawtime[2:3] == ':':                                      # Likely 12:02:2016 15:00:00
            if len(rawtime)>12 and rawtime[13:14] == ":":            #        0123456789+123456789  
                JPGtime = datetime.strptime(rawtime, "%d:%m:%Y %H:%M:%S")
            else:
                JPGtime = datetime.strptime(rawtime.strip(), "%d:%m:%Y")
                JPGtime = JPGtime.date()
        elif rawtime[4:5] == ':':                                    # Likely 2024:12:25 15:00:00
            if len(rawtime)>12 and rawtime[13:14] == ":":            #        0123456789+123456789  
                JPGtime = datetime.strptime(rawtime, "%Y:%m:%d %H:%M:%S")
            else:
                JPGtime = datetime.strptime(rawtime.strip(), "%Y:%m:%d")
                JPGtime = JPGtime.date()
        else:
            JPGtime = ''               
    except Exception as e:
        logger.info(f"Conversion date {rawtime} failed, error: {e}")
        JPGtime = ''
        pass
    return JPGtime

# try different sources
def getJPGtime(JPGobject, MyProject):
    JPGtime = tryJPGtime(JPGobject['EXIF:DateTimeOriginal'], MyProject)
    if JPGtime == "":
        JPGtime = tryJPGtime(MyProject.UniquePictureName_dict[JPGobject['UniquePictureName']]['DateTimeOriginal'], MyProject)
    return JPGtime

# make sure we do not do small twice
def getSmallJPG(filename):
    """'x.jpg' / 'x.JPG' / 'x.jpeg' -> 'x-small.jpg'. Already-small names pass through.

    This USED to be `filename.replace('.jpg', '-small.jpg')`, which is CASE-SENSITIVE:
    an uppercase `X.JPG` came back UNCHANGED — the caller then treated the full-size
    file as the thumbnail. In jpgResizeCopy that meant SAVING THE THUMBNAIL OVER THE
    ORIGINAL: 142 master pictures were overwritten in place by their own 600x450
    render (88 of them the species' BestJPG — the main image on the page). The master
    holds 1,717 uppercase `.JPG` files, so this was live and ongoing, not historical.
    Split the extension; never pattern-match it."""
    stem, ext = os.path.splitext(filename)
    if not ext or ext.lower() not in ('.jpg', '.jpeg'):
        return filename                      # not a JPG — nothing to derive
    if stem.lower().endswith('-small'):
        return filename                      # only once needed
    return stem + '-small.jpg'

def jpgResizeCopy(targetstr, horizontalsize = 600):
    # resize, avoid rounding errors that are case by 499 vs 500
    try:
        if targetstr.lower().endswith('-small.jpg'): return                 # only once needed
        small = getSmallJPG(targetstr)
        # THE INVARIANT: a thumbnail is NEVER written over its source. Case was only
        # how the bug got in; this guard is what makes the class of bug impossible.
        # If the derived name ever equals the source again, we refuse and say so
        # instead of quietly destroying a full-resolution picture.
        if os.path.normcase(os.path.abspath(small)) == os.path.normcase(os.path.abspath(targetstr)):
            logger.warning(f"jpgResizeCopy REFUSED: the thumbnail name for "
                           f"{os.path.basename(targetstr)} is the file itself — "
                           f"writing it would destroy the original.")
            return
        # CACHE: the callers loop over the WHOLE-master allspecies.csv, so a commit of
        # two pictures used to regenerate every species' -small.jpg from scratch. Skip
        # when a thumbnail already exists and is no older than its source — regenerate
        # only when the source changed. (Missing source: fall through so Image.open
        # raises and is logged as before.)
        try:
            if os.path.exists(small) and os.path.getmtime(small) >= os.path.getmtime(targetstr):
                return
        except OSError:
            pass                                 # can't stat — fall through and rebuild
        image = Image.open(targetstr)
        if image.size[0] > image.size[1]:
            resized_image = image.resize((600, 450))
        elif image.size[0] == image.size[1]:
            resized_image = image.resize((600, 600))
        else:
            resized_image = image.resize((450, 600))
        # wpercent = (horizontalsize/float(image.size[0]))
        # hsize = int((float(image.size[1])*float(wpercent)))
        # resized_image = image.resize((horizontalsize, hsize))
        resized_image.save(small)
    except Exception as e:
        logger.info(f"Resizing {targetstr} failed, error: {e}")
    return

def getAltTextForJPG(MyProject, jpgname):
    try:
        jpgobj = MyProject.files_dict[jpgname]
        upn = MyProject.UniquePictureName_dict[jpgobj['UniquePictureName']]
        # Blog extras (landscape/context photos) show the (translated) Headline as
        # the slider title instead of the " /  / " artifact three blank species
        # names would produce. A topic-filed landscape now carries a synthetic
        # species name (the year leaf, e.g. "2026"), so the empty-name test no
        # longer catches it — key off the subject keyword too.
        from engine.speciesclassification import SpeciesClassification
        if upn['WetSoortNaam'] == "" or SpeciesClassification.isBlogExtra(upn.get('subject', "")):
            return getHeadlineText(MyProject, upn).replace('"', '&quot;')
        sci_display = upn['WetSoortNaam'].replace(' ', '&nbsp;').replace('-', '&#8209;').capitalize()
        # Link the scientific name to the species page when it exists in the
        # trip. The alt attribute is double-quoted in the enclosing HTML, so
        # use &quot; for the href's delimiters.
        sci_html = sci_display
        species = MyProject.sp.species_dict.get(upn['WetSoortNaam'])
        if species and int(species.get('iSpeciesCollectionCount', 0)) > 0:
            url = MyProject.getBiotaWebRoot() + species["TargetPath"] + species["ScientificName"] + '-' + MyProject.getLanguage() + '.html'
            sci_html = '<a href=&quot;' + url + '&quot;>' + sci_display + '</a>'
        names = sci_html + ' / ' + \
                upn['EngSoortNaam'].replace(' ', '&nbsp;').replace('-', '&#8209;').capitalize() + ' / ' + \
                upn['NLSoortNaam'].replace(' ', '&nbsp;').replace('-', '&#8209;').capitalize()
    except:
        names = ""
    return names

_src_lang_memo = {}  # anchor text -> detected language, process-local

def _pictureSourceLang(MyProject, picture):
    """Source language for a picture's text, anchored on the longer Description
    (reliable) and applied to the short Headline too — a bare "Strand" detects
    poorly on its own. Falls back to the Headline when there's no Description.
    '' when neither is present or detection is unavailable. Memoized per anchor
    so a picture's headline and description don't each pay a detect round-trip."""
    if not picture:
        return ''
    desc = (picture.get('Description', '') or '').strip()
    if desc[0:1] == "#":
        desc = desc[1:].strip()
    head = (picture.get('Headline', '') or '').strip()
    anchor = desc if desc else head
    if not anchor:
        return ''
    if anchor in _src_lang_memo:
        return _src_lang_memo[anchor]
    try:
        detected = MyProject.ct.detectLanguage(anchor)
        lang = detected.lang if detected else ''
    except Exception:
        lang = ''
    _src_lang_memo[anchor] = lang
    return lang

def getDescriptionText(MyProject, picture):
    """The picture's Description, '#'-stripped and translated to the current
    language (same handling as blog token 41). '' when there is none. Used by
    the blog card body and the slider caption so both stay consistent."""
    if not picture or picture.get('Description', '') == "":
        return ""
    text = picture['Description']
    if text[0:1] == "#":
        text = text[1:]
    try:
        return MyProject.ct.getOneLanguage(text, MyProject.getLanguage(),
                                           source_lang=_pictureSourceLang(MyProject, picture))
    except Exception:
        return text

def getHeadlineText(MyProject, picture):
    """The picture's Headline translated to the current language. Blog extras
    use it as the card/slider title. Anchors the source-language guess on the
    Description (see _pictureSourceLang) so a short Headline isn't mis-detected."""
    if not picture or picture.get('Headline', '') == "":
        return ""
    try:
        return MyProject.ct.getOneLanguage(picture['Headline'], MyProject.getLanguage(),
                                           source_lang=_pictureSourceLang(MyProject, picture))
    except Exception:
        return picture['Headline']

def getCameraSettings(MyProject, picture):
    if picture:
        language = MyProject.getLanguage()  
        if language == "en": metorwith = " with "
        else: metorwith = " met "
        workstr = ""
        if picture['CameraModel'] != "":
            workstr += picture['CameraModel']
            if picture['LensShortName'] != "":
                workstr += metorwith + picture['LensShortName']
            elif picture['LensLongName'] != "":
                workstr += metorwith + picture['LensLongName']
        elif picture['JPGSize'] in ['3024x4032','4032x3024']:
            workstr += 'Apple iPhone'        
        if picture['Settings'] != ' sec at F, ISO , distance ':
            # Settings is built as "<exp> sec at F<f>, ISO <iso>, distance <dist>".
            # Split off the distance so we can (a) drop the clause entirely when
            # the camera reports none (e.g. iPhone, which leaves it empty) and
            # (b) glue the value to its unit with a non-breaking space so
            # "2.59 m" never wraps across lines.
            settings, _, dist = picture['Settings'].partition(', distance ')
            dist = dist.strip()
            if dist and dist != 'inf':
                settings += ', distance ' + dist.replace(' m', '&nbsp;m')
            if language != "en":
                settings = settings.replace('at ', ' bij ').replace('distance', 'afstand')
            if workstr != "": workstr += ", "
            workstr += settings
        return workstr
    else: 
        return ""  
    
def getSpeciesNameScientificDefault(MyProject, speciesname, languageName):
    if MyProject.getSpecies(speciesname)[languageName] != "":
        return MyProject.getSpecies(speciesname)[languageName].capitalize()
    else:
        return MyProject.getSpecies(speciesname)['ScientificName'].capitalize()
    return

def webJoin(*parts):
    """Join URL segments with '/', always.

    A URL is not a filesystem path. `os.path.join` on Windows emits
    '\\trips\\gambia', which is what put backslashes inside published hrefs; on a
    Mac the very same code emits '/trips/gambia'. Same repository, two different
    websites. Everything that builds a link goes through here."""
    segs = []
    for i, p in enumerate(parts):
        p = str(p).replace("\\", "/")
        p = p.rstrip("/") if i == 0 else p.strip("/")
        if p:
            segs.append(p)
    return "/".join(segs)


def webParent(url_path):
    """The containing folder of a web path: '/trips/kreta/' -> '/trips'."""
    return posixpath.dirname(str(url_path).replace("\\", "/").rstrip("/"))


def blogIndexHref(MyProject, BiotaWebRoot):
    """Where a page's "Blogs" link goes: the site's own blog index for a trip or
    album, the trip index for the master (which has no blog of its own). The
    master is recognised as the site whose root IS the master root."""
    master = masterWebRoot(MyProject, BiotaWebRoot)
    language = MyProject.getLanguage()
    if BiotaWebRoot.rstrip("/") == master.rstrip("/"):
        return f"{master}tripindex-{language}.html"
    return f"{BiotaWebRoot}biotablog/blog index-{language}.html"


def masterWebRoot(MyProject, fallback=""):
    """The web root of the MASTER site, whatever project happens to be loaded.

    `MyProject.getBiotaWebRoot()` is the ACTIVE repository's root: with a trip
    loaded it is /trips/<trip>/, with an album /trips/<album>/. That is right for
    a page that lives inside that site, and wrong for a page that lives on the
    master — most of all tripindex-nl/en.html, which the master owns but which
    is regenerated while a trip is active. Resolving those through the active
    root is how /nature/tripindex-nl.html ended up loading its stylesheet from
    /trips/kreta/.

    A trip knows the master root explicitly; an album build stamps it onto the
    project as `master_biota_web_root`. Falls back to the caller's value (the
    active root) only when neither is available — for a master build those are
    the same thing anyway."""
    trip = getattr(MyProject, "Trip", None)
    if trip is not None:
        root = (trip.getMasterBiotaWebRoot() or "").strip()
        if root:
            return root
    root = (getattr(MyProject, "master_biota_web_root", "") or "").strip()
    return root or fallback


def formatToken(tokenno, MyProject, species_param, file, picture):
    global config
    BiotaWebRoot = MyProject.getBiotaWebRoot()

    species_local = species_param      	# default species avoids crashes
    family_local = {}
    order_local = {}
    class_local = {}
    phylum_local = {}
    kingdom_local = {}
    # default output
    workstr = ""
    if species_param != None and species_param != '':
        if species_param['Level'] == Level.KINGDOM:
            kingdom_local = species_param
            species_local = ""
        elif species_param['Level'] == Level.PHYLUM:
            phylum_local = species_param
            kingdom_local = MyProject.getSpecies(phylum_local['Up']) 
        elif species_param['Level'] == Level.CLASS:
            class_local = species_param
            phylum_local = MyProject.getSpecies(class_local['Up'])          
            kingdom_local = MyProject.getSpecies(phylum_local['Up'])  
        elif species_param['Level'] == Level.ORDER:
            order_local = species_param
            class_local = MyProject.getSpecies(order_local['Up'])                  
            phylum_local = MyProject.getSpecies(class_local['Up'])          
            kingdom_local = MyProject.getSpecies(phylum_local['Up'])  
        elif species_param['Level'] == Level.FAMILY:
            family_local = species_param
            if MyProject.getSpecies(family_local['Up'])['Level'] == Level.CLASS:
                class_local = MyProject.getSpecies(family_local['Up'])
            else:
                order_local = MyProject.getSpecies(family_local['Up'])                          
                class_local = MyProject.getSpecies(order_local['Up'])    
            phylum_local = MyProject.getSpecies(class_local['Up'])          
            kingdom_local = MyProject.getSpecies(phylum_local['Up'])  
        elif species_param['Level'] == Level.SPECIES:
            # species_local = species_param                         # Already defaulted
            if species_local['Up'] != "":                           # Linked, assume all levels are ok
                family_local = MyProject.getSpecies(species_local['Up']) 
                if MyProject.getSpecies(family_local['Up'])['Level'] == Level.CLASS:
                    class_local = MyProject.getSpecies(family_local['Up'])
                else:
                    order_local = MyProject.getSpecies(family_local['Up'])                          
                    class_local = MyProject.getSpecies(order_local['Up'])                  
                phylum_local = MyProject.getSpecies(class_local['Up'])
                kingdom_local = MyProject.getSpecies(phylum_local['Up'])
    _obscure = family_local.get('ScientificName', '').lower() in OBSCURE_FAMILIES
    # A blog-extra card (landscape/context photo) has a picture but no species;
    # its title slot shows the XMP:Headline instead of a taxon name. The body
    # (Description, settings, "shows the environment" prose) is handled by the
    # existing no-kingdom branches of tokens 41/42/111/112.
    # A topic-filed landscape now resolves to a synthetic species (the year leaf),
    # so species_param is no longer None for it — also treat a subject-marked blog
    # extra as one, so its title slot shows the Headline, not the "2026" leaf name.
    # `picture` is a dict only on a per-picture card; the biota index/summary
    # pages pass a string, so guard on isinstance before .get() (the headline
    # path below already needs a dict anyway).
    from engine.speciesclassification import SpeciesClassification
    is_blog_extra = isinstance(picture, dict) and (
        species_param is None
        or SpeciesClassification.isBlogExtra(picture.get('subject', "")))
    if is_blog_extra and tokenno in (25, 36, 37, 125):
        headline = getHeadlineText(MyProject, picture)
        if tokenno == 125:
            # 3-line title slot — headline only, padded for consistent card height
            return headline + "<br>&nbsp;<br>&nbsp;"
        return headline
    try:
        # orders
        if tokenno == 1:   
            return order_local['DutchName'].capitalize() if order_local else ""
        elif tokenno == 2: return order_local['ScientificName'].strip('0123456789 ').capitalize() if order_local else ""
        elif tokenno == 3: 
            return order_local['EnglishName'].capitalize() if order_local else ""
        elif tokenno == 4: return order_local['iPictureCollectionCount'] if order_local else ""
        elif tokenno == 7:     # lijst met ordes (Nederlands of Engels)
            language = MyProject.getLanguage()
            if language == 'nl': languageName = 'DutchName'
            else: languageName = 'EnglishName'                
            if species_param['Level'] == Level.SPECIES and len(class_local)>0:
                workstr = "<li class=""\"first""\">\n"
                for leaf in MyProject.orderedLeaves(class_local['Leaves']):
                    leaf_sp = MyProject.getSpecies(leaf)
                    # Only link taxa that actually have a page in THIS web tree;
                    # on a trip/album the tree holds a subset of the master, so a
                    # master collection count (getSpeciesCount) would emit 404s.
                    if MyProject.hasWebPage(leaf_sp):
                        workstr += '			<li><a href="' + BiotaWebRoot + \
                            leaf_sp['TargetPath'] + \
                            leaf_sp['ScientificName'] + '-' + language + '.html">' \
                            + getSpeciesNameScientificDefault(MyProject, leaf, languageName) + "</a></li>\n"
                return workstr
            else:
                return ""
        # family
        elif tokenno == 11: return family_local['DutchName'].capitalize() if family_local else ""
        elif tokenno == 12: return family_local['ScientificName'].strip('0123456789 ').capitalize() if family_local else ""
        elif tokenno == 13: return family_local['EnglishName'].capitalize() if family_local else ""
        elif tokenno == 14: return family_local['iPictureCollectionCount'] if family_local else ""
        elif tokenno == 18:     # lijst met ordes (Nederlands)
            language = MyProject.getLanguage()
            if language == 'nl': languageName = 'DutchName'
            else: languageName = 'EnglishName'   
            if species_param['Level'] == Level.SPECIES and len(family_local)>0:
                workstr = "<li class=""\"first""\">\n"
                for leaf in MyProject.orderedLeaves(family_local['Leaves']):
                    if _folded_subspecies(MyProject, leaf):
                        continue
                    leaf_sp = MyProject.getSpecies(leaf)
                    # Only link siblings that actually have a page in THIS web
                    # tree; on a trip/album the tree holds a subset of the
                    # master, so guarding on the master collection count would
                    # emit dead links to species not published for this trip.
                    if MyProject.hasWebPage(leaf_sp):
                        workstr += "			<li><a href=""\"" + BiotaWebRoot + \
                            leaf_sp['TargetPath'] + \
                            leaf_sp['ScientificName']  + '-' + language + '.html">' \
                            + getSpeciesNameScientificDefault(MyProject, leaf, languageName) + "</a></li>\n"
                return workstr
            else:
                return ""
        # species
        elif tokenno == 21: return species_local['DutchName'].capitalize() if species_local else ""
        elif tokenno == 22: return formatSubspecies(species_local['ScientificName'].strip('0123456789 ')).capitalize() if species_local else ""
        elif tokenno == 23: return species_local['EnglishName'].capitalize() if species_local else ""
        elif tokenno == 24: return species_local['iPictureCollectionCount'] if species_local else ""
        elif tokenno == 25: # Nederlands (Engels) of Nederlands of Engels  of wetenschappelijk
            language = MyProject.getLanguage()
            sci_25 = formatSubspecies(species_local['ScientificName'].strip('0123456789 ')).capitalize()
            if species_local['DutchName'] != "" and language == 'nl':
                return species_local['DutchName'].capitalize() + " (" + sci_25 + ")"
            elif species_local['EnglishName'] != "" and language == 'en':
                return species_local['EnglishName'].capitalize() + " (" + sci_25 + ")"
            else:
                return sci_25
        elif tokenno == 30:
            # Convert time.time() to timestamp to a datetime object
            date = datetime.fromtimestamp(time.time())
            # Format the datetime object as "Wed Jan 24, 2024"
            return date.strftime("a %b %d, %Y")   
        elif tokenno == 31:
            #return species_param['DutchName'].capitalize() + ' ' + species_param['ScientificName'].strip('0123456789 ').capitalize()
            return formatSubspecies(species_param['ScientificName'].strip('0123456789 ')).capitalize()
        elif tokenno == 32:                                 # one level lower (enkelvoud)
            return ClassificationGroupName[species_param['Level'].value-2].capitalize()
        elif tokenno == 33:                                 # one level lower (enkelvoud)
            if MyProject.getLanguage() == "nl":
                languageName = 'DutchName'
                groupname = ClassificationGroupNameNL[species_param['Level'].value-1]
            else: 
                languageName = 'EnglishName'
                groupname = ClassificationGroupName[species_param['Level'].value-1]
            if species_param[languageName] != "":
                return groupname + ': ' + species_param[languageName].capitalize()
            else:
                return groupname + ': ' + formatSubspecies(species_param['ScientificName'].strip('0123456789 ')).capitalize()
        elif tokenno == 34:                                 # meervoud voor de children, dus 1 extra er af daarom -2                   
            return ClassificationGroupNameNLmeervoud[species_param['Level'].value-2].lower()
        elif tokenno == 35:
            workstr =  ClassificationGroupNameNL[species_param['Level'].value-1] if MyProject.getLanguage() == "nl" else ClassificationGroupNameNL[species_param['Level'].value-1] 
            workstr +=  ' ' + formatSubspecies(species_param['ScientificName'].strip('0123456789 ')).capitalize()
            languageName = 'DutchName' if MyProject.getLanguage() == "nl" else 'EnglishName'
            if species_param[languageName] != "":
                workstr += ' (' + species_param['DutchName'].capitalize() + ')'
            if workstr == 'Biota Biota (Biota)':
                language = MyProject.getLanguage()          # intended language
                if language == 'nl':
                    workstr = 'alle leven (biota)'
                else:
                    workstr = workstr = 'all life (biota)'
            return workstr            
        elif tokenno == 36:
            # Dutch → English → scientific, so the blog photo title= never
            # collapses to a blank when the Dutch name is missing.
            return (
                species_param['DutchName'].capitalize()
                or species_param['EnglishName'].capitalize()
                or formatSubspecies(species_param['ScientificName'].strip('0123456789 ')).capitalize()
            )
        elif tokenno == 37:
            # English → Dutch → scientific.
            return (
                species_param['EnglishName'].capitalize()
                or species_param['DutchName'].capitalize()
                or formatSubspecies(species_param['ScientificName'].strip('0123456789 ')).capitalize()
            )
        elif tokenno == 38:                                 # meervoud voor de children, dus 1 extra er af daarom -2                   
            return ClassificationGroupNameENmeervoud[species_param['Level'].value-2]   
        elif tokenno == 39:                                 # one level lower (enkelvoud)
            return ClassificationGroupNameNL[species_param['Level'].value-2].lower()
        elif tokenno == 40:
            return getFormattedLocation(picture, _obscure, altitude=True) if picture else ""
        elif tokenno == 41:
            # Same '#'-strip + description-anchored translation as the slider.
            return getDescriptionText(MyProject, picture)
        elif tokenno == 42:
            return getCameraSettings(MyProject, picture)
        elif tokenno == 43:# 	case 43: // was 60 = totaal aantal soorten
            return MyProject.getSpeciesCount('biota')
        elif tokenno == 44:
            return BiotaWebRoot + picture['TargetPath'] + picture['BestJPG']
        elif tokenno == 45:
            return MyProject.getBlogDateFromPictures() + '/' + file['File:FileName']    #BUGBUG
        elif tokenno == 46:
            try:
                return getJPGtime(file, MyProject).strftime('%Y:%m:%d %H:%M:%S')
            except:
                return ""
        elif tokenno == 47:
            return MyProject.getBlogDateFromPictures() + '/' + getSmallJPG(file['File:FileName'])   #BUGBUG        
        elif tokenno == 48:
            # Only the trip index uses this, and that page is the MASTER's: its
            # banner must not follow whichever trip was open when it was
            # regenerated — deleting that trip's pages broke it (2026-10-12).
            workstr = masterWebRoot(MyProject, BiotaWebRoot) + "includes web/keerkringvogel.jpg"
            return workstr
        elif tokenno == 49: # banner, now by kingdom, but could be more specific
            if not MyProject.isMasterRepository():
                try:
                    workstr = MyProject.Trip.getTripBanner()
                except:
                    workstr = BiotaWebRoot + "includes web/keerkringvogel.jpg"
            elif kingdom_local == {}: 
                workstr = BiotaWebRoot + "includes web/keerkringvogel.jpg"
            else:
                if kingdom_local['ScientificName'].lower() == "animalia":
                    workstr = BiotaWebRoot + "includes web/keerkringvogel.jpg"
                elif kingdom_local['ScientificName'].lower() == "fungi":
                    workstr = BiotaWebRoot + "includes web/paddenstoelen.jpg"
                else:
                    workstr = BiotaWebRoot + "includes web/planten.jpg"
            return workstr
        elif tokenno == 50: # vogels
            return MyProject.getSpeciesCount('aves')
        elif tokenno == 51: # insecten
            return MyProject.getSpeciesCount('insecta')
        elif tokenno == 52: # planten
            return MyProject.getSpeciesCount('plantae')
        elif tokenno == 53: # fungi
            return MyProject.getSpeciesCount('fungi')     
        elif tokenno == 54: # fungi
            return MyProject.getSpeciesCount('mammalia')            
        elif tokenno == 60:                                 # one level lower (enkelvoud)
            return ClassificationGroupLidwoord[species_param['Level'].value-2].lower()
        elif tokenno == 71: return class_local['DutchName'].capitalize() 									 
        elif tokenno == 72: return class_local['ScientificName'].strip('0123456789 ').capitalize()						
        elif tokenno == 73: return class_local['EnglishName'].capitalize()
        elif tokenno == 74: return class_local['iPictureCollectionCount'] 		

        elif tokenno == 76: return phylum_local['DutchName'].capitalize()								
        elif tokenno == 77: return phylum_local['ScientificName'].strip('0123456789 ').capitalize()								
        elif tokenno == 78: return phylum_local['EnglishName'].capitalize()
        elif tokenno == 79: return phylum_local['iPictureCollectionCount']

        elif tokenno == 81: return kingdom_local['DutchName'].capitalize()						
        elif tokenno == 82: return kingdom_local['ScientificName'].strip('0123456789 ').capitalize()
        elif tokenno == 83: return kingdom_local['EnglishName'].capitalize()
        elif tokenno == 84: return kingdom_local['iPictureCollectionCount']
        elif tokenno == 89:
            if MyProject.Trip != None:
                return MyProject.Trip.getMasterBiotaWebRoot()     # path to current main repository, NOT to trip also for subdirectories
            else:
                return BiotaWebRoot
        elif tokenno == 90:
            return BiotaWebRoot                     # path to current main repository or to trip
        elif tokenno == 92:
            # Web root of the MASTER site. Use this, not %%90%%, on any page the
            # master owns but that may be rendered while a trip/album is loaded.
            return masterWebRoot(MyProject, BiotaWebRoot)
        elif tokenno == 181:
            # The "Blogs" / "Recent" link. A trip or album has its own blog
            # index; the master has none (nothing writes nature/biotablog/ —
            # the copy on the server was a 2024 leftover that never updated), so
            # there it opens the trip index, which lists every trip's blog.
            return blogIndexHref(MyProject, BiotaWebRoot)
        elif tokenno == 91:
            language = MyProject.getLanguage()
            if MyProject.Trip != None:
                directory = MyProject.Trip.getMasterBiotaWebRoot()     # path to current main repository, NOT to trip also for subdirectories
            workstr = "	<h4>Links</h4>\n	<div class=""\"content\""">\n		<ul class= ""\"w3-ul w3-hoverable\""">\n"
            workstr += '			<li class=""\"first\"""><a href="'+ blogIndexHref(MyProject, BiotaWebRoot) +'">Recent</a></li>\n'
            if MyProject.Trip != None:
                workstr += '            <li><a href="' + directory + 'tripindex-' + language + '.html">Trips</a></li>'
                if language == 'nl':
                    soorten = 'Trip soorten'
                    biota = 'Trip Biota'
                else:
                    soorten = 'Trip species'
                    biota = 'Trip Biota'
            else:
                if language == 'nl':
                    soorten = 'Alle soorten'
                    biota = 'Biota: Leven'
                else:
                    soorten = 'All species'
                    biota = 'Biota: Life'                    
            if language == 'nl':
                workstr += '			<li><a href= "'+ BiotaWebRoot +'summaries/all biota species-nl.html">' + soorten + '</a></li>\n'
                workstr += "			<li><a href= ""\"" + BiotaWebRoot + "biota/biota-nl.html" + "\""">" + biota + "</a></li>\n"
                languageName = 'DutchName'
            else:
                workstr += '			<li><a href= "'+ BiotaWebRoot +'summaries/all biota species-en.html">' + soorten + '</a></li>\n'
                workstr += "			<li><a href= ""\"" + BiotaWebRoot + "biota/biota-en.html" + "\""">" + biota + "</a></li>\n"
                languageName = 'EnglishName'                
            if len(kingdom_local)>0: 
                rijk = 'Rijk: ' if language == 'nl' else 'Kingdom: '
                workstr += "			<li><a href= ""\"" + BiotaWebRoot + kingdom_local['TargetPath'] + kingdom_local['ScientificName'] + '-' + language + '.html">' + rijk + \
                    (kingdom_local[languageName].capitalize() if kingdom_local[languageName] != "" else  kingdom_local['ScientificName'].capitalize()) + "</a></li>\n"
            if len(phylum_local)>0:
                stam = 'Stam: ' if language == 'nl' else 'Phylum: '
                workstr += "			<li><a href= ""\"" + BiotaWebRoot + phylum_local['TargetPath'] + phylum_local['ScientificName']  + '-' + language + '.html">' + stam + \
                    (phylum_local[languageName].capitalize() if phylum_local[languageName] != "" else phylum_local['ScientificName'].capitalize()) + "</a></li>\n"
            if len(class_local)>0:
                klasse = 'Klasse: ' if language == 'nl' else 'Class: '                
                workstr += "			<li><a href= ""\"" + BiotaWebRoot + class_local['TargetPath'] + class_local['ScientificName'] + '-' + language + '.html">' + klasse + \
                    (class_local[languageName].capitalize() if class_local[languageName] != "" else class_local['ScientificName'].capitalize()) + "</a></li>\n"
            if len(order_local)>0:
                orde = 'Orde: ' if language == 'nl' else 'Order: '                       
                workstr += "			<li><a href= ""\"" + BiotaWebRoot + order_local['TargetPath'] + order_local['ScientificName'] + '-' + language + '.html">' + orde + \
                    (order_local[languageName].capitalize() if order_local[languageName] != "" else order_local['ScientificName'].capitalize()) + "</a></li>\n"
            if len(family_local)>0:
                fam = 'Familie: ' if language == 'nl' else 'Family: '                
                workstr += "			<li><a href= ""\"" + BiotaWebRoot + family_local['TargetPath'] + family_local['ScientificName'] + '-' + language + '.html">' + fam + \
                    (family_local[languageName].capitalize() if family_local[languageName] != "" else family_local['ScientificName'].strip('0123456789 ').capitalize()) + "</a></li>\n"
            workstr += "		</ul>\n	</div>\n"
            return workstr
        elif tokenno == 93:         # Plaatjes en namen, zelfde voor beide talen
            language = MyProject.getLanguage()
            workstr = "<div id=""\"grid\""">\n"
            for speciesname in MyProject.orderedLeaves(species_param['Leaves']):
                if speciesname not in MyProject.sp.species_dict or _folded_subspecies(MyProject, speciesname):
                    continue
                species = MyProject.sp.species_dict[speciesname]
                if (int(species['iPicturesWithJPGsCollection']) + int(species['iPicturesWithJPGsThisRun'])) > 0:
                    workstr += "			  <div>\n"
                    workstr += '			     <a href="' + BiotaWebRoot + species["TargetPath"] + species["ScientificName"] + '-' + language + '.html\"' + '>\n'
                    if species['BestJPG'] != "" and species['BestJPGPathName'] != "":
                        # loading="lazy" defers off-screen image fetches so the all-species
                        # grid (~2k entries) doesn't trigger a 2k-request fetch storm at
                        # page load and time out the browser. decoding="async" keeps
                        # decode work off the main thread.
                        workstr += '			         <img src="' + \
                            BiotaWebRoot + species['BestJPGPathName'].replace("E:/Dropbox/my birds","/biota/animalia/chordata/aves") + species['BestJPG'] + \
                            '" loading="lazy" decoding="async"' + " style=""\"width:100%\""">\n"
                    workstr += "			     </a>" 
                    workstr += '<p>' + species['ScientificName'].strip('0123456789 ').capitalize() + '</p>'
                    workstr += '<p>' + species['EnglishName'].capitalize() + '</p>'
                    workstr += '<p>' + species['DutchName'].capitalize() + '</p>\n'                    
                    workstr += "\n"
                    workstr += "              </div>\n"
            workstr += "            </div>\n"
            return workstr
        elif tokenno == 94:          # page for order
            workstr = BiotaWebRoot + species_param["TargetPath"] + species_param["ScientificName"]
            return workstr
        elif tokenno == 95:          # page for species
            workstr = BiotaWebRoot + species_param["TargetPath"] + species_param["ScientificName"]
            return workstr
        elif tokenno == 100:         # Nederlandse of Engelse plaatjes en namen
            workstr = addOneSpeciesPage(MyProject, species_param)              
            return workstr
        elif tokenno == 101:         # Male/Female etc
            try:
                language = MyProject.getLanguage()
                if language == 'nl':
                    if picture['malefemale'] != '' or picture['ageintext']:                    
                        workstr = "De foto toont een "
                        if picture['malefemale'] == 'male':
                            workstr += "mannetje. "
                        elif picture['malefemale'] == 'female':
                            workstr += "vrouwtje. "
                        elif picture['malefemale'] == 'couple':
                            workstr += 'paartje. '
                        elif picture['ageintext'] == 'juv.':
                            workstr += 'jonge ' + picture['NLSoortNaam'].lower() + '.'
                        elif picture['ageintext'] == 'imm.':
                            workstr += 'onvolwassen ' +  + picture['NLSoortNaam'].lower() + '.'                     
                    if picture['summerwinter'] == 'br.' and 'aves' in picture['TargetPath']:
                        workstr += ' De vogel is in broedkleed (zomerkleed).'                        
                    if picture['summerwinter'] == 'non-br.' and 'aves' in picture['TargetPath']:
                        workstr += ' De vogel is in winterkleed.'   
                elif language == 'en':
                    if picture['malefemale'] != '' or picture['ageintext']:
                        workstr = "The photo shows a "
                        if picture['malefemale'] == 'male':
                            workstr += "male. "
                        elif picture['malefemale'] == 'female':
                            workstr += "female. "
                        elif picture['malefemale'] == 'couple':
                            workstr += "couple."
                        elif picture['ageintext'] == 'juv.':
                            workstr += 'young '  + picture['EngSoortNaam'].lower() + '.'
                        elif picture['ageintext'] == 'imm.':
                            workstr += 'immature ' + picture['EngSoortNaam'].lower() + '.'                 
                    if picture['summerwinter'] == 'br.' and 'aves' in picture['TargetPath']:
                        workstr += ' The bird is in breeding (summer) plumage.'                        
                    if picture['summerwinter'] == 'non-br.' and 'aves' in picture['TargetPath']:
                        workstr += ' The vogel is in winter plumage.'   
            except:
                workstr = ""                    
            return workstr
        elif tokenno == 110 or tokenno == 113:         # blog achtige samenvatting
            language = 'nl' if tokenno == 113 else 'en'
            BestJPGs = MyProject.Blog.BestJPGs
            if len(BestJPGs) == 0:
                # step 1: collect selected species
                selected_dict = {}
                excluded = addSpeciesToSummaryPost(MyProject, species_param['ScientificName'], selected_dict)
                MyProject.Blog.excluded_species = excluded
                # also fold in non-species "blog extras" (landscape/context
                # photos) for the full day post — they have no taxon so the
                # species-tree walk above never reaches them. Only for the
                # whole-day blog (root group); a narrower group filter like
                # "aves" excludes landscapes, which belong to no group.
                if MyProject.Blog.getSelectedSpeciesGroup() in ("", "biota"):
                    collectBlogExtras(MyProject, selected_dict)
                # step 2: order the cards. Normal photos run chronologically;
                # obscured (location-hidden) species are pulled OUT of the time
                # line — their chronological neighbours would otherwise give away
                # their location — and appended LAST in taxonomic order under the
                # "Protected species" heading. Their time is suppressed too
                # (tokens 111/112) so it can't be correlated with nearby GPS.
                normal = {f: t for f, t in selected_dict.items() if not isObscureJPG(MyProject, f)}
                protected = [f for f in selected_dict if isObscureJPG(MyProject, f)]
                def _tax_key(f):
                    upn = MyProject.UniquePictureName_dict[MyProject.files_dict[f]['UniquePictureName']]
                    return ((upn.get('TargetPath') or '').lower(), f)
                ordered = list(dict(sorted(normal.items(), key=itemgetter(1)))) + sorted(protected, key=_tax_key)
                # step 3: generate the webpage
                workstr = renderBlogPhotos(MyProject, ordered, language)
            else:
                # Re-render from the stored order (already normal-then-obscured).
                workstr = renderBlogPhotos(MyProject, BestJPGs, language)
            return workstr
        elif tokenno == 111:         # Nederlandse tekst
            # mogelijke tekst
            suppressnames = False
            if picture['Description'] != "":
                if picture['Description'][0:1] == "#":
                    suppressnames = True
            workstr =  ""
            # species and relationship (skipped for non-species blog extras)
            if kingdom_local != {}:
                if not (species_local['ScientificName'][0:5] == "homo " or suppressnames):
                    workstr += "De foto toont een "
                    sci_nl = formatSubspecies(species_local['ScientificName'].strip('0123456789 ')).capitalize()
                    sci_html = makeLinkSpeciesDisplay(MyProject, BiotaWebRoot, species_local, "<i>" + sci_nl + "</i>")
                    if species_local['DutchName'] != "":
                        workstr += species_local['DutchName'].capitalize() + " (" + sci_html + "). "
                    else:
                        workstr += sci_html + ". "
                    workstr += "Het is een "
                    if kingdom_local['ScientificName'] == "animalia":
                        if class_local['ScientificName'] == "aves":
                            workstr += "vogel"
                        elif class_local['ScientificName'] == "insecta":
                            workstr += "insect"
                        else:
                            workstr += "dier"
                    elif kingdom_local['ScientificName'] == "fungi":
                        workstr += "schimmel"
                    else:
                        workstr += "plant"
                    if order_local == {}:
                        workstr += " uit de klasse van de <i>"
                        workstr = makeLinkScienticficName(MyProject, BiotaWebRoot, class_local, workstr)
                    else:
                        workstr += " uit de orde van de <i>"
                        workstr = makeLinkScienticficName(MyProject, BiotaWebRoot, order_local, workstr)
                    workstr += "</i> en de familie <i>"
                    workstr = makeLinkScienticficName(MyProject, BiotaWebRoot, family_local, workstr)
                    workstr +=  "</i>."
            # location/date — runs for species AND blog extras, so a landscape
            # gets the real place/time instead of "De foto toont de omgeving."
            try:
                parsed_date = getJPGtime(file, MyProject)
                workstr += " De foto is genomen"
                if getFormattedLocation(picture, _obscure) != "":
                    workstr += " bij " + getFormattedLocation(picture, _obscure, altitude=True)
                if not _obscure and parsed_date.strftime('%H:%M') not in ["","00:00"]:
                    workstr += " om " + parsed_date.strftime('%H:%M')
                if parsed_date.strftime('%d %b, %Y') != "":
                    workstr +=  " op " + parsed_date.strftime('%d %b, %Y')  + "."
            except:
                logger.info(f'Location or date error for {file["File:FileName"]}: Location  {file["EXIF:DateTimeOriginal"]} and  {file["IPTC:Keywords"]}')
                pass
            return workstr.strip()
        elif tokenno == 112:         # Engelse tekst
            suppressnames = False
            if picture['Description'] != "":
                if picture['Description'][0:1] == "#":
                    suppressnames = True
            workstr =  ""
            # species and relationship (skipped for non-species blog extras)
            if kingdom_local != {}:
                if not (species_local['ScientificName'][0:5] == "homo " or suppressnames):
                    workstr = "The photo shows a "
                    sci_en = formatSubspecies(species_local['ScientificName'].strip('0123456789 ')).capitalize()
                    sci_html = makeLinkSpeciesDisplay(MyProject, BiotaWebRoot, species_local, "<i>" + sci_en + "</i>")
                    if species_local['EnglishName'] != "":
                        workstr += species_local['EnglishName'].capitalize() + " (" + sci_html + "). "
                    else:
                        workstr += sci_html + ". "
                    workstr += "It is a "
                    if kingdom_local['ScientificName'] == "animalia":
                        if class_local['ScientificName'] == "aves":
                            workstr += "bird"
                        elif class_local['ScientificName'] == "insecta":
                            workstr += "insect"
                        else:
                            workstr += "animal"
                    elif kingdom_local['ScientificName'] == "fungi":
                        workstr += "fungus"
                    else:
                        workstr += "plant"
                    if order_local == {}:
                        workstr += " in the class <i>"
                        workstr = makeLinkScienticficName(MyProject, BiotaWebRoot, class_local, workstr)
                    else:
                        workstr += " in the order <i>"
                        workstr = makeLinkScienticficName(MyProject, BiotaWebRoot, order_local, workstr)
                    workstr += "</i> and the family <i>"
                    workstr = makeLinkScienticficName(MyProject, BiotaWebRoot, family_local, workstr)
                    workstr +=  "</i>."
            # location/date — runs for species AND blog extras, so a landscape
            # gets the real place/time instead of "The photo shows the environment."
            try:
                parsed_date = getJPGtime(file, MyProject)
                workstr += " The photo was taken"
                if getFormattedLocation(picture, _obscure) != "":
                    workstr += " at " + getFormattedLocation(picture, _obscure, altitude=True)
                if not _obscure and parsed_date.strftime('%H:%M') not in ["","00:00"]:
                    workstr += " at " + parsed_date.strftime('%H:%M')
                if parsed_date.strftime('%A %B %d, %Y') != "":
                    workstr += " on " + parsed_date.strftime('%A %B %d, %Y')  + "."
            except:
                pass
            return workstr.strip()
        elif tokenno == 114:
            return MyProject.getBiotaBlogLanguageSwitch().lower()
        elif tokenno == 115:
            return MyProject.Blog.getBlogDay()
        elif tokenno == 116:
            return MyProject.Blog.getBlogTitel()
        elif tokenno == 117:
            return MyProject.Blog.getBlogTitle()
        elif tokenno == 118:
            return MyProject.Blog.getBlogBericht()
        elif tokenno == 119:
            return MyProject.Blog.getBlogMessage()                       
        elif tokenno == 120:         # all soorten
            workstr = ""
            workstr = addAllSpeciesWrapper(MyProject, species_param['ScientificName'], workstr)
            return workstr
        elif tokenno == 121 or tokenno == 122:
            language = 'nl' if tokenno == 121 else 'en'
            # Row labels follow the page language, like the date label below and
            # like the trip index (token 146/147) already did. These two used to
            # be hard-coded Dutch, so the English blog index read "Titel".
            if language == 'nl':
                title, soorten = 'Titel', 'Soorten'
            else:
                title, soorten = 'Title', 'Species'
            workstr = ''
            for logname in MyProject.Blog.getBlogDictSortedKeys():
                blog = MyProject.Blog.getBlog(logname)
                # print(f'Logname {logname} {blog["BestJPGs"][0]}')
                workstr += '   <div class="w3-row" style="margin: 15px">\n'
                workstr += '      <div class="w3-col w3-win8-olive" style="width:30%">\n'
                if blog["BestJPGs"] == []:
                    workstr += '          <p>(picture not found)</p>\n'
                elif blog["SelectedGroup"] == '':
                    # this is a reference to a blog index, not to a day/project
                    targetlink = webJoin(webParent(blog['BestJPGs']), 'biotablog',
                                         'blog index-' + language + '.html')
                    workstr += f'          <a href="' + targetlink + '"><img\n'
                    workstr += '            src="' + blog['BestJPGs'] + '"\n'
                    workstr += '            class="w3-border" style="padding:8px;max-width:100%" title="Click to go to ' + blog["title_" + language] + '"></a>\n'                    
                else:
                    # pict the best picture from the current pictures in the blog
                    if blog['blogjpg'] == "":
                        bestjpg = str(MyProject.Blog.getBestJPGinList(logname))
                    else:  # override for preferred picture on index
                        bestjpg = blog['blogjpg']
                    altforjpg = getAltTextForJPG(MyProject, bestjpg)
                    workstr += f'          <a href="{logname}-slider-' + language + '.html"><img\n'
                    workstr += '            src="' + logname + '/' +  bestjpg + '" alt="' + altforjpg + '"\n'
                    workstr += '            class="w3-border" style="padding:8px;max-width:100%" title="Click to open ' + blog["title_" + language] + '"></a>\n'
                workstr += '      </div>\n'
                workstr += '      <div class="w3-col" style="width:70%">\n'
                workstr += '        <div class="content" style="margin-left:10px">\n'
                workstr += '          <table class="w3-table w3-hoverable">\n'
                workstr += '            <tr class="w3-win8-olive">\n'
                if blog["SelectedGroup"] == '':
                    workstr += '              <th>' + title + '</th><td><a href="' + targetlink + '">' + blog["title_" + language] + '</a></td>\n'
                else:
                    # Name the href like targetlink above, so ONE f-string owns the
                    # substitution. The f used to sit on the '<th>' fragment, which
                    # holds no placeholder, while the '{logname}' sat in a plain
                    # literal beside it — so every day title on the blog index linked
                    # to the file "{logname}-nl.html", which cannot exist.
                    daylink = f'{logname}-{language}.html'
                    workstr += '              <th>' + title + '</th><td><a href="' + daylink + '">' + blog["title_" + language] + '</a></td>\n'
                workstr += '            </tr>\n'
                workstr += '            <tr>\n'
                workstr += '              <th></th><td>' + blog["message_" + language] + '</td>\n'
                workstr += '            </tr>\n'
                if blog["SelectedGroup"] != 'biota' and blog["SelectedGroup"] != "":
                    workstr += '            <tr>\n'
                    workstr += '              <th>' + soorten + '</th><td>' + blog["SelectedGroup"].capitalize() + '</td>\n'
                    workstr += '            </tr>\n'
                workstr += '            <tr>\n'
                if blog["SelectedGroup"] != "":
                    if language == 'nl':
                        workstr += '              <th>Datum:</th><td>' + blog["blog"][14:24] + '</td>\n'
                    else:
                        workstr += '              <th>Date:</th><td>' + blog["blog"][14:24] + '</td>\n'
                workstr += '            </tr>\n'                  
                workstr += '          </table>\n'
                workstr += '        </div>\n'
                workstr += '      </div>\n'
                workstr += '    </div>\n'
            return workstr
        elif tokenno == 125:
            # Always output 3 lines (local name, English/Dutch, scientific) for consistent card height
            sci_display = formatSubspecies(species_local['ScientificName'].strip('0123456789 ')).capitalize()
            sexe_age = formatSexe(picture) + formatAgePlumage(picture)
            dutch = species_local['DutchName']
            english = species_local['EnglishName']
            is_nl = MyProject.getLanguage() == 'nl'
            # Determine primary and secondary common names
            primary = dutch if is_nl else english
            secondary = english if is_nl else dutch
            if primary:
                line1 = primary.capitalize() + sexe_age
                line2 = "<i><small>" + secondary.capitalize() + "</small></i>" if secondary else "&nbsp;"
                line3 = "<i><small>" + sci_display + "</small></i>"
            elif secondary:
                line1 = secondary.capitalize() + sexe_age
                line2 = "&nbsp;"
                line3 = "<i><small>" + sci_display + "</small></i>"
            else:
                line1 = "<i>" + sci_display + "</i>" + sexe_age
                line2 = "&nbsp;"
                line3 = "&nbsp;"
            return line1 + "<br>" + line2 + "<br>" + line3
        elif tokenno == 126:
            w =  MyProject.Blog.getColumnWidth()
            return str(w)
        # elif tokenno == 141:
        #     return MyProject.Blog.getBlogDay()
        elif tokenno == 142:
            return MyProject.Trip.getTripTitel()
        elif tokenno == 143:
            return MyProject.Trip.getTripTitle()
        elif tokenno == 144:
            return MyProject.Trip.getTripBericht()
        elif tokenno == 145:
            return MyProject.Trip.getTripMessage()  
        elif tokenno == 146 or tokenno == 147:
            if tokenno == 146:
                language = 'nl'
                soorten = 'Trip soorten'
                title = 'Titel'
            else:
                language = 'en'
                soorten = 'Trip species'
                title = 'Title'                
            workstr = ''
            # A new master has no trips yet, and unadvertising the last one
            # empties the list: say so, instead of a page that ends mid-air.
            trip_dict = MyProject.Trip.trip_dict if MyProject.Trip is not None else {}
            if not trip_dict:
                if language == 'nl':
                    return '<p>Er zijn nog geen trips gepubliceerd.</p>\n'
                return '<p>No trips have been published yet.</p>\n'
            # Every trip lives beside the active one, so the folder that holds
            # them is the active repository's parent ('/trips/kreta/' -> '/trips').
            # This page is the master's, but it is regenerated while a trip or
            # album is loaded, which is what makes that derivation work. If it
            # ever runs from the master itself the parent is '/', and silently
            # emitting '/braakman' would be worse than saying so.
            trips_folder = webParent(BiotaWebRoot)
            # A trip without its own picture gets the site's default one —
            # never an <img> pointing at the trip folder (a broken image).
            default_picture = masterWebRoot(MyProject, BiotaWebRoot) + "includes web/tripimage.jpg"
            if trips_folder in ("", "/"):
                # '/nature/' -> '/', i.e. the master itself: there is no sibling
                # folder to hang the trips off.
                logger.warning(
                    "Trip index: cannot tell where the trips live from web root "
                    f"'{BiotaWebRoot}' — trip links would be wrong. Regenerate "
                    "this page with a trip or album loaded.")
            # The sort key tolerates a missing or malformed todate by
            # sending that row to the bottom — one bad row must not kill
            # the whole page.
            def _todate_key(trip_obj):
                val = (trip_obj.get('todate') or '').strip()
                try:
                    return datetime.strptime(val, '%Y-%m-%d')
                except ValueError:
                    return datetime.min
            sorted_items = sorted(trip_dict.items(),
                                  key=lambda x: _todate_key(x[1]),
                                  reverse=True)

            for tripname in sorted_items:  # MyProject.Trip.getTripDictSortedKeys()
                trip = MyProject.Trip.getTrip(tripname[0])
                TripWebRoot = webJoin(trips_folder, trip['trip_name'])
                # A trip without a title still needs link text: its name. An
                # empty title left only "(Trip soorten)" (2026-10-12).
                shown_title = ((trip.get("title_" + language) or "").strip()
                               or trip['trip_name'].capitalize())
                # print(f'Logname {logname} {blog["BestJPGs"][0]}')
                workstr += '   <div class="w3-row" style="margin: 15px">\n'
                workstr += '      <div class="w3-col w3-win8-olive" style="width:25%">\n'
                if trip['trip_name'] == "":
                    workstr += '          <p>(picture not found)</p>\n'
                else:
                    picture = (trip.get("trip_picture") or "").strip()
                    src = TripWebRoot + '/' + picture if picture else default_picture
                    tip = html.escape(shown_title, quote=True)
                    workstr += f'          <a href="'  + TripWebRoot + '/biotablog/blog index-' + language + '.html"><img\n'
                    workstr += '            src="' + src + '" alt="' + tip + '"\n'
                    workstr += '            class="w3-border" style="padding:8px;max-width:100%" title="' + tip + '"></a>\n'
                workstr += '      </div>\n'
                workstr += '      <div class="w3-col" style="width:75%">\n'
                workstr += '        <div class="content" style="margin-left:10px">\n'
                workstr += '          <table class="w3-table w3-hoverable">\n'
                workstr += '            <tr class="w3-win8-olive">\n'
                workstr += f'              <th>' + title + '</th><td><a href="' + TripWebRoot + '/biotablog/blog index-' + language + '.html">' + shown_title + '</a> '
                workstr += f'<a href="' + TripWebRoot + '/biota/biota-' + language + '.html">(' + soorten + ')</a></td>\n'
                workstr += '            </tr>\n'
                workstr += '            <tr>\n'
                workstr += '              <th></th><td>' + trip["message_" + language] + '</td>\n'
                workstr += '            </tr>\n'
                workstr += '            <tr>\n'                
                if language == 'nl':
                    workstr += '          <th>Periode:</th><td>' + trip["fromdate"] + ' tot ' + trip["todate"] + '</td>\n'
                else:
                    workstr += '          <th>Period:</th><td>' + trip["fromdate"] + ' to ' + trip["todate"] + '</td>\n'
                workstr += '            </tr>\n'                  
                workstr += '          </table>\n'
                workstr += '        </div>\n'
                workstr += '      </div>\n'
                workstr += '    </div>\n'
            return workstr
        elif tokenno == 148 or tokenno == 149:            # banner link
            language = 'nl' if tokenno == 148 else 'en'
            directory = masterWebRoot(MyProject, BiotaWebRoot)
            workstr = '<li><a href="' + directory + 'tripindex-' + language + '.html">Trips</a></li>'
            return workstr
        elif tokenno == 150 or tokenno == 151:
            language = 'nl' if tokenno == 150 else 'en'
            if MyProject.Trip != None:
                tn = MyProject.Trip.getTripName()
                if tn in MyProject.Trip.trip_dict:
                    workstr = MyProject.Trip.getTrip(tn)['title_' + language]
                else:
                    workstr = tn.capitalize()
            return workstr
        elif tokenno == 152 or tokenno == 153:
            language = 'nl' if tokenno == 152 else 'en'
            if MyProject.Trip != None:
                if language == 'nl':
                    workstr = "Alle soorten in "
                else:
                    workstr = "All species seen in " 
                tn = MyProject.Trip.getTripName()
                if tn in MyProject.Trip.trip_dict:
                    workstr += MyProject.Trip.getTrip(tn)['title_' + language]
                else:
                    workstr += tn.capitalize()
            else:
                if language == 'nl':
                    workstr = "Alle soorten"
                else:
                    workstr = "All species"                    
            return workstr            
        # 160-162: functions for slider pages
        elif tokenno == 160:
            pictures = len(MyProject.Blog.BestJPGs)
            count = 1
            workstr = ""
            for jpgs in MyProject.Blog.BestJPGs:
                workstr += '        <div class="mySlides w3-win8-olive">\n'
                workstr += '            <div class="numbertext">' + str(count) + ' / ' + str(pictures) + '</div>\n'
                workstr += '            <img src="' + MyProject.Blog.blogday + '/' + jpgs  + '" style="padding:8px;width:100%">\n'
                workstr += '        </div>\n'          
                count += 1  
            return workstr
        elif tokenno == 161:
            pictures = len(MyProject.Blog.BestJPGs)
            count = 1
            workstr = ""            
            for jpgname in MyProject.Blog.BestJPGs:
                picture = MyProject.UniquePictureName_dict[MyProject.files_dict[jpgname]['UniquePictureName']]
                # Description shown below the title for both species and blog
                # extras; '&quot;' so it can't terminate the alt="" attribute.
                desc = getDescriptionText(MyProject, picture)
                desc_html = ('<p>' + desc.replace('"', '&quot;') + '</p>') if desc else ''
                workstr += '        <div class="column">\n'
                workstr += '            <img class="demo cursor" src="' + MyProject.Blog.blogday + '/' + jpgname.replace('.jpg', '-small.jpg')  + '" style="width:100%"\n'
                workstr += '                 onclick="currentSlide(' + str(count) + ')" alt="<h3>' + getAltTextForJPG(MyProject, jpgname) +  '</h3>' + desc_html + getCameraSettings(MyProject, picture) + '">\n'
                workstr += '        </div>\n'
                count += 1
            return workstr
        elif tokenno == 162:
            pictures = len(MyProject.Blog.BestJPGs)
            if pictures == 0:
                return ""
            else:
                if pictures < 8:
                    return str(round((1/pictures)*100, 1))
                else:
                    # groups = (pictures + 7) // 8 .. other numbers than 8 give (rounding?) errors
                    groupsize = 8
                    return str(round((1/groupsize)*100, 1))
        elif tokenno == 163:    # link to blog slider
            return MyProject.getBlogDateFromPictures() + '-slider-' + MyProject.getLanguage() + '.html?slide=' + str(MyProject.Blog.CountJPG)
        elif tokenno == 164:    # All / Trip spacies
            language = MyProject.getLanguage()
            if MyProject.Trip == None:
                directory = MyProject.getBiotaWebRoot()     # path to current main repository, NOT to trip also for subdirectories 
                if language == 'nl': 
                    tripormaster = 'Alle Soorten'  
                else: tripormaster = 'All Species'  
            else:
                directory = BiotaWebRoot
                if language == 'nl': 
                    tripormaster = 'Trip Soorten'  
                else: tripormaster = 'Trip Species'                  
                
            workstr = '<li><a href="' + directory + 'summaries/all biota species-' + language + '.html">' + tripormaster + '</a></li>'         
            return workstr        
        elif tokenno == 170:
            workstr = MyProject.breadcrumb.makeCrumb('test')
            return workstr
        elif tokenno == 171:         # closing navigation sentence (Top / Previous / Next)
            return buildBlogNavSentence(MyProject)
        elif tokenno == 172:         # slider navigation (Previous / Next slider pages)
            return buildSliderNavSentence(MyProject)
        elif tokenno == 185:         # species page: its subspecies, as tiles
            if species_param.get('Level') != Level.SPECIES:
                return ""
            subspecies = _subspecies_with_pages(MyProject, species_param)
            if not subspecies:
                return ""
            language = MyProject.getLanguage()
            name = 'DutchName' if language == 'nl' else 'EnglishName'
            photos = " foto's" if language == 'nl' else ' photos'
            workstr = '<h3>' + ('Ondersoorten' if language == 'nl' else 'Subspecies') + '</h3>\n<div id="grid">\n'
            for ssp in subspecies:
                count = int(ssp['iPictureCollectionCount'] or 0) + int(ssp['iPictureThisRunCount'] or 0)
                workstr += ('  <div>\n    <a href="' + BiotaWebRoot + ssp['TargetPath'] + ssp['ScientificName']
                            + '-' + language + '.html">\n')
                if ssp['BestJPG'] and ssp['BestJPGPathName']:
                    workstr += ('      <img src="' + BiotaWebRoot + ssp['BestJPGPathName'] + getSmallJPG(ssp['BestJPG'])
                                + '" loading="lazy" decoding="async" style="width:100%">\n')
                workstr += '    </a>'
                workstr += '<p>' + (ssp[name] or ssp['ScientificName']).capitalize() + '</p>'
                workstr += '<p><i>' + ssp['ScientificName'].capitalize() + '</i></p>'
                workstr += '<p>' + str(count) + photos + '</p>\n  </div>\n'
            return workstr + '</div>\n'
        elif tokenno == 186:         # table under the names: subspecies, or "subspecies of"
            if species_param.get('Level') != Level.SPECIES:
                return ""
            parent = _subspecies_of(MyProject, species_param.get('ScientificName', ''))
            node = MyProject.sp.species_dict.get(parent) if parent else None
            if node is not None and MyProject.hasWebPage(node):
                rows, of = [node], True
            else:
                rows, of = _subspecies_with_pages(MyProject, species_param), False
            if not rows:
                return ""
            language = MyProject.getLanguage()
            if language == 'nl':
                caption = 'Ondersoort van' if of else 'Ondersoorten'
                heads = ('Nederlands', 'Engels', 'Wetenschappelijk', "Foto's")
                first, second = 'DutchName', 'EnglishName'
            else:
                caption = 'Subspecies of' if of else 'Subspecies'
                heads = ('English', 'Dutch', 'Scientific', 'Photos')
                first, second = 'EnglishName', 'DutchName'
            workstr = ('<TABLE class="w3-table-all w3-hoverable" style="margin-top:12px">\n'
                       '  <CAPTION><em>' + caption + '</em></CAPTION>\n'
                       '  <thead><tr class="w3-light-grey">'
                       + ''.join('<th>' + h + '</th>' for h in heads) + '</tr></thead>\n')
            for n in rows:
                count = int(n['iPictureCollectionCount'] or 0) + int(n['iPictureThisRunCount'] or 0)
                href = BiotaWebRoot + n['TargetPath'] + n['ScientificName'] + '-' + language + '.html'
                workstr += ('  <tr><td><a href="' + href + '">' + (n[first] or n['ScientificName']).capitalize()
                            + '</a></td><td>' + (n[second] or '').capitalize() + '</td><td><i>'
                            + n['ScientificName'].capitalize() + '</i></td><td>' + str(count) + '</td></tr>\n')
            return workstr + '</TABLE>\n'
        elif tokenno == 180:         # published occurrence map (Maps Phase 4)
            # "Where I have seen this species" — a Leaflet map over the species'
            # own master pictures. Returns "" for an obscure family (a published
            # map is exactly where a protected location leaks) and for a species
            # with no usable coordinate, so the token is safe on every page.
            from engine.webmap import speciesOccurrenceMap
            return speciesOccurrenceMap(MyProject, species_local or species_param,
                                        MyProject.getLanguage())
        else:
            return '<token ' + tokenno + '>'
    except Exception as e:
        logger.info(f"{'<Failed to handle token ' + str(tokenno) + '>'} Error: {e}")
        # {species_param['ScientificName']}:
        return '<Failed to handle token '  + str(tokenno) + '>'

# logic selected species
def writeOneSpecies(MyProject, leaf, temptextfile):
    species = MyProject.sp.species_dict[leaf]
    if species["TargetPath"] == "":
        logger.warning(f"{species['ScientificName']} has no TargetPath, review naming for parents.")
        return
    #print(f"working {species['ScientificName']} {species['EnglishName']}")
    language = MyProject.getLanguage()
    BiotaWebRoot = MyProject.getBiotaWebRoot()
    # When the best-pick JPG is not among this build's local files — e.g. a
    # virtual-trip per-day extract carries the master data row (so BestJPG is set)
    # but not the actual JPG file — link the species page and image to the shared
    # master biota web tree, where the page and -small JPG already exist, instead
    # of the local trip tree (which has nothing to copy/render). Mirrors how the
    # blog renders shared master pictures. For a normal/master build the file IS
    # local, so this stays BiotaWebRoot unchanged.
    species_web_root = BiotaWebRoot
    if species['BestJPG'] and species['BestJPG'] not in MyProject.files_dict:
        master_web = getattr(MyProject, "master_biota_web_root", "") or ""
        if master_web:
            species_web_root = master_web
    try:
        print('                <div>\n			        <a href="' + \
            species_web_root + species["TargetPath"] + species["ScientificName"] +  '-' + language + '.html' + '">\n', file=temptextfile)
        if species['BestJPG'] != "" and species['BestJPGPathName'] != "":
            print('			            <img src="' + species_web_root + \
            species['TargetPath'] + getSmallJPG(species['BestJPG']) + '" style=\"width:100%\">\n			        </a>', file=temptextfile)
        # texts for 3 languages in 3 lines
        print('<p>' + species['ScientificName'].strip('0123456789 ').capitalize() + '</p>', file=temptextfile)
        print('<p>' + species['EnglishName'].capitalize() + '</p>', file=temptextfile)
        print( '<p>' + species['DutchName'].capitalize() + '</p>', file=temptextfile)
        # end
        print( '                </div>\n', file=temptextfile)
    except Exception as e:
        logger.info(f"writeOneSpecies '{leaf}' render failed: {e}")
        # be sure the JPG is included, unless we do want it / need it
    if not MyProject.getSkipJPGs() and species['BestJPG'] in MyProject.files_dict:
        sourcestr = MyProject.files_dict[species['BestJPG']]['WindowsFileName']
        targetstr = os.path.join(MyProject.ReviewFrozen(),species['TargetPath'], species['BestJPG'])
        try:
            os.makedirs(os.path.join(MyProject.ReviewFrozen(), species['TargetPath']), exist_ok=True)
            # Skip the copy when the source is already at its final
            # destination in the repository's biota tree (i.e. previously
            # committed historical files in master mode). sv7's
            # copytree(dirs_exist_ok=True) only adds/updates from frozen,
            # so omitting these from frozen leaves master/biota intact.
            if _src_already_at_final(MyProject, sourcestr,
                                     species['TargetPath'], species['BestJPG']):
                MyProject.addAllSpeciesFiles(sourcestr, os.path.join(
                    MyProject.getBiotaWebRoot(), species['TargetPath'], species['BestJPG']))
            else:
                rc = copy_with_retry(sourcestr, targetstr)
                # Register the FROZEN biota copy (targetstr), not the transient
                # source (sourcestr), as the -small.jpg resize source. sourcestr
                # can be a temp drop-pool path (sv_droppool_…) or a trip-local
                # file; resizing there writes the thumbnail somewhere that never
                # reaches the repository biota tree, so putRecursiveTreeDisk's
                # copytree(frozen/biota -> repository/biota) leaves the local
                # thumbnail missing until a full rebuild. targetstr is the real,
                # persistent full-size copy in the frozen biota tree, so the
                # -small.jpg lands beside it and is carried into biota/ on commit.
                MyProject.addAllSpeciesFiles(targetstr,os.path.join(MyProject.getBiotaWebRoot(), species['TargetPath'], species['BestJPG']))
        except Exception as e:
            logger.info(f"writeOneSpecies copy failed {sourcestr} -> {targetstr}: {e}")
    else:
        pass
        #print(f"Done with text")
    #print(f"Done with function")        
    return

# using a temp file to avoid pass around very large strings
# not sure this matters
def addAllSpeciesWrapper(MyProject, speciesname, textCreated):
    try:
        with tempfile.SpooledTemporaryFile(mode='w+t', encoding='utf-8') as temptextfile:
            try:
                addAllSpeciesPage(MyProject, speciesname, temptextfile)
            except Exception as e:
                logger.info(f"addAllSpeciesPage('{speciesname}') failed: {e}")
            temptextfile.seek(0)
            textlist = temptextfile.readlines()
            textCreated += '\n'.join(textlist)
            temptextfile.close()
            # write — ensure the frozen/ dir exists first. In Publish Day the
            # website build (sv5, which calls this) runs BEFORE Freeze (sv6)
            # creates frozen/, so on a fresh day this open() used to fail silently
            # (swallowed below) → sv7 WriteCommit then crashed reading a missing
            # allspecies.csv. A writer must create its own target directory.
            os.makedirs(MyProject.ReviewFrozen(), exist_ok=True)
            with open(os.path.join(MyProject.ReviewFrozen(), 'allspecies.csv'), mode="w", newline="", encoding='utf-8') as allspeclist:
                for file in MyProject.getAllSpeciesFiles():
                    ftpfile = MyProject.AllSpeciesFiles[file]
                    print(f'"{file}", "{ftpfile}"', file=allspeclist)
    except Exception as e:
        logger.warning(f"addAllSpeciesWrapper('{speciesname}') failed: {e}")
    return textCreated

# create overview page for all species, in order of classification
def addAllSpeciesPage(MyProject, speciesname, temptextfile):
    leaves = MyProject.orderedLeaves(MyProject.getSpecies(speciesname)['Leaves'])
    for leaf in leaves:
        species = MyProject.sp.species_dict[leaf]
        if _folded_subspecies(MyProject, leaf):
            continue
        if (int(species['iPicturesWithJPGsCollection']) + int(species['iPicturesWithJPGsThisRun'])) > 0:
            if species['Level'] == Level.SPECIES:
                # Suppress orphan species cards: when a picture is renamed
                # off a species the iPicturesWithJPGs* counters can stay >0
                # while BestJPG/BestJPGPathName drop to "". writeOneSpecies
                # would then emit an unclosed <a> wrapping empty <p>s —
                # visible as the blank/broken cells in the all-biota grid.
                if not species['BestJPG'] or not species['BestJPGPathName']:
                    logger.warning(
                        f"all-species grid: suppressed '{species['ScientificName']}' "
                        f"(stale counters {species['iPicturesWithJPGsCollection']}+"
                        f"{species['iPicturesWithJPGsThisRun']} but BestJPG empty)")
                    continue
                writeOneSpecies(MyProject, leaf, temptextfile)
            else:
                textCreated = addAllSpeciesPage(MyProject, leaf, temptextfile)

    return

def addOneSpeciesPage(MyProject, species_param):
    BiotaDiskRoot = MyProject.getBiotaAllorNewDir()
    workstr = ""
    # a smarter selection is useful, especially to avoid very similar pictures
    try:
        rateorder = {}
        skipped_files = 0
        skipped_upn = 0
        skipped_wrong_species = 0
        # the species page shows ALL its pictures: its own and its subspecies'
        subspecies = _subspecies_with_pages(MyProject, species_param)
        allowed = {species_param['ScientificName']} | {ssp['ScientificName'] for ssp in subspecies}
        jpgmap = list(species_param['JPGmap'])
        for ssp in subspecies:
            jpgmap += list(ssp['JPGmap'])
        for JPGname in jpgmap:
            basename = os.path.basename(JPGname)
            if basename not in MyProject.files_dict:
                skipped_files += 1
                continue
            JPGname_no_path = MyProject.files_dict[basename]
            upn_key = JPGname_no_path['UniquePictureName']
            if upn_key not in MyProject.UniquePictureName_dict:
                skipped_upn += 1
                continue
            # Drop ghost JPGmap entries: the UPN's species was reassigned (e.g. via
            # the Commit dialog) but JPGmap kept the old reference. Trying to
            # render those produces a path under the wrong species and a copy
            # error. Only show JPGs whose UPN currently belongs to this species.
            picture = MyProject.UniquePictureName_dict[upn_key]
            if picture.get('WetSoortNaam', '') not in allowed:
                skipped_wrong_species += 1
                continue
            diversity = MyProject.getPictureAgeSexePlumage(picture)
            if picture.get('WetSoortNaam', '') != species_param['ScientificName']:
                # a subspecies is its own group, like male/female, so its
                # pictures show beside the species' own (not only when they
                # happen to out-rate them)
                diversity = picture['WetSoortNaam'] + ' ' + diversity
            rating = MyProject.computeRatingFile(JPGname_no_path)
            if '-small' not in JPGname:
                rateorder[(diversity, rating, JPGname)] = JPGname
        if skipped_files > 0 or skipped_upn > 0 or skipped_wrong_species > 0:
            logger.debug(f"addOneSpeciesPage '{species_param['ScientificName']}': JPGmap={len(species_param['JPGmap'])}, "
                         f"matched={len(rateorder)}, skipped_files={skipped_files}, skipped_upn={skipped_upn}, "
                         f"skipped_wrong_species={skipped_wrong_species}")
        # keep all JPGs sorted, not yet by male/female, breeding etc
        # Sort the dictionary by rating in descending order
        # Sort the items by diversity, rating, and JPGname in descending order
        sorted_items = sorted(rateorder.items(), key=lambda item: (item[0][0], item[0][1], item[0][2]), reverse=True)

        # Create a defaultdict to group items by diversity
        grouped_items = defaultdict(list)
        for (diversity, rating, JPGname), value in sorted_items:
            grouped_items[diversity].append((rating, JPGname, value))

        diversity_groups = len(grouped_items)
        maxpicturesperAgeSexePlumage = 2
        if diversity_groups == 1:
            maxpicturesperAgeSexePlumage = 4
        elif diversity_groups == 2:
            maxpicturesperAgeSexePlumage = 3

        # make sure only use the first occurrance of an UPN, which is the best (ie, with the highest rate)

        # print(f'{len(sorted_items)} JPGs for {species_param['DutchName']}/{species_param['ScientificName']} with {len(species_param['JPGmap'])}')            
        for diversity, items in grouped_items.items():
            filesfound = 0
            usedUPNs = {}
            rejectUPNs = 0            
            for rating, JPGname, value in items:
                basename = os.path.basename(JPGname)
                if basename not in MyProject.files_dict:
                    continue
                JPGname_no_path = MyProject.files_dict[basename]
                if (JPGname_no_path != "") and (JPGname_no_path['UniquePictureName'] not in usedUPNs.keys()):
                    # do we have a picture close to it
                    dropit = False
                    try:
                        JPGtime = getJPGtime(JPGname_no_path, MyProject)
                        for UPNshownsofar in usedUPNs:
                            try:
                                showntime = usedUPNs[UPNshownsofar]
                                deltahours = abs(showntime - JPGtime).total_seconds()/3600
                                if deltahours < 0.25: dropit = True
                            except:
                                dropit = True
                    # more conditions we can try                        
                    except Exception as e:
                        logger.info(f"""Pass error "{JPGname_no_path['WindowsFileName']}" with "{species_param['ScientificName']}" / "{species_param['DutchName']}": {e}""")
                    # possibly missing date
                    if not dropit:
                        usedUPNs[JPGname_no_path['UniquePictureName']] = JPGtime
                        for line in MyProject.getTemplateLines("vogelfoto.html"):
                            workstr += getTemplateOutputString(MyProject, species_param, JPGname_no_path,
                                                                MyProject.UniquePictureName_dict[JPGname_no_path['UniquePictureName']], line)
                        picture = MyProject.UniquePictureName_dict[JPGname_no_path['UniquePictureName']]
                        targetstr =  os.path.join(BiotaDiskRoot, picture['TargetPath'], picture['BestJPG'])
                        JPGname_no_path = MyProject.files_dict[os.path.basename(JPGname)]
                        if not MyProject.getSkipJPGs():
                            # Skip the redundant frozen-staging copy when
                            # the source is already at its final spot in
                            # master/biota (typical for historical files
                            # in master mode).
                            if not _src_already_at_final(MyProject,
                                    JPGname_no_path['WindowsFileName'],
                                    picture['TargetPath'], picture['BestJPG']):
                                copy_with_retry(JPGname_no_path['WindowsFileName'], targetstr)
                        filesfound += 1
                    else:
                        rejectUPNs += 1

                #print(species_param['ScientificName'], "\n", workstr)
                if (filesfound == maxpicturesperAgeSexePlumage):
                    break   # go to next man/female/young/breeding etc
    except Exception as e:
        logger.warning(f"""Error in species page "{species_param['ScientificName']}" / "{species_param['DutchName']}": {e}""")
    if not workstr:
        logger.debug(f"addOneSpeciesPage: no images for '{species_param['ScientificName']}', JPGmap has {len(species_param['JPGmap'])} entries")
    return workstr

# create summary page, that should become Wordpress
# also test best picture function.
# the first def is recursively calling itself, the second is the package
def addSpeciesToSummaryPost(MyProject, speciesname, selected_species, excluded=None):
    if excluded is None:
        excluded = {"no_jpg": [], "below_score": []}
    try:
        species = MyProject.sp.species_dict[speciesname]
        for leaf in species['Leaves']:
            species = MyProject.sp.species_dict[leaf]
            if int(species['iPictureThisRunCount']) > 0:            # brute force scan
                if species['Level'] == Level.SPECIES:
                    if MyProject.Blog.getSelectedSpeciesGroup() in species['TargetPath'] and \
                    (MyProject.Blog.getOnlyNewSpecies() and int(species['iSpeciesThisRunCount']) > 0) or (not MyProject.Blog.OnlyNewSpecies):
                        if MyProject.Blog.getMinimumScore() <= species['currentHighestRating']:                 # Last is BUGBUG if high score is from past??
                            # picture species and time
                            collectBestJPGs(MyProject, leaf, selected_species)
                        elif species['currentHighestRating'] == 0:
                            excluded["no_jpg"].append(leaf)
                        else:
                            excluded["below_score"].append((leaf, species['currentHighestRating']))
                else:
                    addSpeciesToSummaryPost(MyProject, leaf, selected_species, excluded)
    except Exception as e:
        logger.warning(f"addSpeciesToSummaryPost('{speciesname}') failed: {e}")
    return excluded

# species['iPictureThisRunCount'] tells me if the picture is seen for this species, but we do not yet
# know what is best picture for that species, because that use the overall best picture.  Also, that
# does not include male/female info.
# so ... we need a more complex algorithm:
# 1. Inspect the pictures with JPGs within the time window (from blog.from_time/last_time)
# 2. Add the "getPictureAgeSexePlumage" to the name for uniqueness, 
#    so one species can generate two pictures (adult/juv, male/female, breeding)
# 3. sort the best pictures of each type
def _rate(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def collectBestJPGs(MyProject, species_scientific_name, selected_species):
    try:
        candidate_jpgs = {}
        #print(f"collectBestJPGs ok: {species_scientific_name}  selected species {selected_species}")

        for upn_iter in MyProject.UniquePictureName_dict:
            upn_item = MyProject.UniquePictureName_dict[upn_iter]
            # select only this species, that have a JPG, and that are within this time range 
            if upn_item['WetSoortNaam'] == species_scientific_name:
                if upn_item['BestJPG'] != "":
                    if upn_item['DateTimeOriginal'] >= MyProject.Blog.from_time and upn_item['DateTimeOriginal'] <= MyProject.Blog.last_time:
                        sortname = species_scientific_name + MyProject.getPictureAgeSexePlumage(upn_item)
                        info = [upn_item['BestJPG'], upn_item['Rate']]; 
                        if sortname in candidate_jpgs:
                            candidate_jpgs[sortname].append(info)
                        else:
                            candidate_jpgs[sortname] = [info]
        # see if we have different sex / age / plumage: the best N for each one
        # (N = the blog's "pictures per species", 1 unless set — 2026-10-21).
        # Highest rating first; equal ratings keep the order they were shot in.
        n = getattr(MyProject.Blog, "MaxPerSpecies", 1) or 1
        for candidate in candidate_jpgs:
            ranked = sorted(candidate_jpgs[candidate], key=lambda it: -_rate(it[1]))
            for jpg, _r in ranked[:n]:
                # remember the JPG we want to use (keyed by time → shown in time order)
                selected_species[jpg] = MyProject.UniquePictureName_dict[
                    MyProject.files_dict[jpg]['UniquePictureName']]['DateTimeOriginal']
    except Exception as e:
        logger.warning(f"collectBestJPGs('{species_scientific_name}') failed: {e}")
    return

def collectBlogExtras(MyProject, selected_species):
    """Add non-species "blog extras" (landscape/context photos) to the day's
    summary post. They carry no taxon, so addSpeciesToSummaryPost's tree walk
    never reaches them. Key each by capture time (like collectBestJPGs) so they
    interleave chronologically with the species cards."""
    # Local import avoids a module-load cycle (projectlib <-> websupport).
    from engine.speciesclassification import SpeciesClassification
    try:
        for upn_item in MyProject.UniquePictureName_dict.values():
            if upn_item['BestJPG'] == "":
                continue
            if not SpeciesClassification.isBlogExtra(upn_item['subject']):
                continue
            if MyProject.Blog.from_time <= upn_item['DateTimeOriginal'] <= MyProject.Blog.last_time:
                selected_species[upn_item['BestJPG']] = upn_item['DateTimeOriginal']
    except Exception as e:
        logger.warning(f"collectBlogExtras failed: {e}")
    return

def addOneSpeciesJPG(MyProject, jpgname, textCreated, templatehtml):
    try:
        filedata = MyProject.files_dict[jpgname]
        picturedata = MyProject.UniquePictureName_dict[filedata['UniquePictureName']]
        # Blog extras render with species=None so the card template falls back to
        # Headline/Description and the environment text. A landscape now carries a
        # topics/ leaf as its WetSoortNaam (so the master can file it), but it must
        # STILL render as an extra here — a topic leaf is not a taxon.
        from engine.topics import is_topic_node
        wetname = picturedata['WetSoortNaam']
        species = MyProject.sp.species_dict.get(wetname) \
            if wetname and not is_topic_node(MyProject.sp.species_dict, wetname) else None
        MyProject.Blog.addBestJPG(filedata)
        # print(f"Picture {picturedata['NLSoortNaam']} {picturedata['UniquePictureName']} {picturedata['Description']}")
        for rawline in MyProject.getTemplateLines(templatehtml):
            textCreated += getTemplateOutputString(MyProject, species, filedata, picturedata, rawline.rstrip('\n')) + "\n"
        # copy a picture in subdirectory, named after file
        sourcestr = filedata['WindowsFileName']
        targetstr = MyProject.getBiotaBlogDir() + MyProject.Blog.getBlogDay() + '/' + filedata['File:FileName']
        #print(f'from {sourcestr} to {targetstr}')
        if not MyProject.getSkipJPGs():
            copy_with_retry(sourcestr, targetstr)
            if species is not None and isObscure(MyProject, species):
                result = no_window.run([exiftool_exe(), '-GPS:all=', '-overwrite_original', targetstr],
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                if '1 image files updated' in result.stdout:
                    logger.info(f"🔒 GPS stripped from species for which the location must be hidden (obscured): {os.path.basename(targetstr)}")
                else:
                    logger.warning(f"GPS strip failed for {targetstr}: {result.stdout.strip()} {result.stderr.strip()}")
            jpgResizeCopy(targetstr, horizontalsize = 600)
        if MyProject.getLanguage() == 'en':
            # once per species — with several pictures per species it repeated
            logged = MyProject.Blog.__dict__.setdefault('_rated_logged', set())
            if species is not None:
                if species['ScientificName'] not in logged:
                    logged.add(species['ScientificName'])
                    logger.info(f"Rated {species['currentHighestRating']:3}  {species['EnglishName']:32}  Species path {species['TargetPath']}")
            else:
                logger.info(f"Blog extra        {picturedata.get('Headline', ''):32}  {filedata['File:FileName']}")
    except Exception as e:
        logger.warning(f"addOneSpeciesJPG failed for '{jpgname}': {e}")
    return textCreated

def isObscureJPG(MyProject, jpgname):
    """True if this JPG's picture belongs to an obscure family (location must
    be hidden). Detected via TargetPath so it needs no species lookup; matches
    OBSCURE_FAMILIES and the trip-overview lock marker."""
    try:
        upn = MyProject.UniquePictureName_dict[MyProject.files_dict[jpgname]['UniquePictureName']]
        tp = (upn.get('TargetPath') or '').lower()
        return any(fam in tp for fam in OBSCURE_FAMILIES)
    except Exception:
        return False

def protectedSectionHeading(language):
    """Full-width grid divider introducing the protected-species block.
    grid-column:1/-1 spans every column of the #grid in blogtext-<lang>.html."""
    if language == 'nl':
        title = "Beschermde soorten"
        note = "Locatie en tijd zijn weggelaten om deze soorten te beschermen."
    else:
        title = "Protected species"
        note = "Exact location and time are withheld to protect these species."
    return ('<div style="grid-column:1/-1;text-align:left">'
            '<h2>' + title + '</h2><p>' + note + '</p></div>\n')

def renderBlogPhotos(MyProject, ordered_files, language):
    """Render the day's photo cards in `ordered_files`, inserting the
    protected-species heading once, just before the first obscured photo.
    Obscured photos are always ordered last (token 110/113), so the heading
    lands at the boundary on both a fresh build and a re-render from BestJPGs."""
    workstr = ""
    heading_done = False
    for jpgname in ordered_files:
        if not heading_done and isObscureJPG(MyProject, jpgname):
            workstr += protectedSectionHeading(language)
            heading_done = True
        workstr = addOneSpeciesJPG(MyProject, jpgname, workstr, "oneblogphoto-" + language + ".html")
        MyProject.Blog.CountJPG += 1
    return workstr