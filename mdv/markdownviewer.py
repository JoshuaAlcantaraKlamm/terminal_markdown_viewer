#!/usr/bin/env python
# coding: utf-8

"""_
# Usage:

    mdv [-t THEME] [-T C_THEME] [-i] [-x] [-X Lexer] [-l] [-L] [-c COLS] \
            [-f FROM] [-m] [-C MODE] [-M DIR] [-H] [-A] [-b TABL] [MDFILE]

# Options:

    MDFILE    : Path to markdown file
    -t THEME  : Key within the color ansi_table.json. 'random' accepted.
    -T C_THEME: Theme for code highlight. If not set: Using THEME.
    -l        : Light background (not yet supported)
    -L        : Display links
    -x        : Do not try guess code lexer (guessing is a bit slow)
    -X Lexer  : Default lexer name (default: python). Set -x to use it always.
    -f FROM   : Display FROM given substring of the file.
    -m        : Monitor file for changes and redisplay FROM given substring
    -M DIR    : Monitor directory for markdown file changes
    -c COLS   : Fix columns to this (default: your terminal width)
    -C [MODE] : Sourcecode highlighting mode
    -A        : Strip all ansi (no colors then)
    -b [TABL] : Set tab_length to sth. different than 4 [default: 4]
    -i        : Show theme infos with output
    -H        : Print html version

# Notes:

- We use stty tool to derive terminal size. If you pipe into mdv we use
  80 cols.
- Setting tab_length away from 4 violates
  [markdown](https://pythonhosted.org/Markdown/). But since many editors allow
  to produce such source we allow it via that flag.

## Themes

environ variables `$MDV_THEME` and `$MDV_CODE_THEME` are understood:

    export MDV_THEME=729.8953; mdv foo.md


## To use mdv.py as lib:

Call the main function with markdown string at hand to get a
formatted one back. Sorry then for no Py3 support, accepting PRs if they
don't screw Py2.

## FROM:

FROM may contain max lines to display, seperated by colon.
Example:

    -f 'Some Head:10' -> displays 10 lines after 'Some Head'

If the substring is not found we set it to the *first* character of the file -
resulting in output from the top (if your terminal height can be derived
correctly through the stty cmd).

## Code Highlighting

Set -C <all|code|doc|mod> for source code highlighting of source code files.
Mark inline markdown with a '_' following the docstring beginnings.

- all: Show markdown docstrings AND code (default, if you say e.g. -C.)
- code: Only Code
- doc: Only docstrings with markdown
- mod: Only the module level docstring


## File Monitor:

If FROM is not found we display the whole file.

## Directory Monitor:

We check only text file changes, monitoring their size.

By default .md, .mdown, .markdown files are checked but you can change like
`-M 'mydir:py,c,md,'` where the last empty substrings makes mdv also monitor
any file w/o extension (like 'README').

### Running actions on changes:

If you append to `-M` a `'::<cmd>'` we run the command on any change detected
(sync, in foreground).

The command can contain placeholders:

    _fp_     # Will be replaced with filepath
    _raw_    # Will be replaced with the base64 encoded raw content
               of the file
    _pretty_ # Will be replaced with the base64 encoded prettyfied output

Like: mdv -M './mydocs:py,md::open "_fp_"'  which calls the open
command with argument the path to the changed file.


## Theme rollers:


    mdv -T all:  All available code styles on the given file.
    mdv -t all:  All available md   styles on the given file.
                If file is not given we use a short sample file.

So to see all code hilite variations with a given theme:

Say C_THEME = all and fix THEME

Setting both to all will probably spin your beach ball...


"""


import io
import os
import sys
import textwrap
is_app = 0
# code analysis for hilite:
try:
    from pygments import lex, token
    from pygments.lexers import get_lexer_by_name
    from pygments.lexers import guess_lexer as pyg_guess_lexer
    have_pygments = True
except ImportError:
    have_pygments = False

import time
import markdown
import re
from docopt import docopt
import markdown.util
from markdown.util import etree
from markdown.extensions.tables import TableExtension
from random import randint
from tabulate import tabulate
from json import loads
from markdown.treeprocessors import Treeprocessor
from markdown.extensions import Extension, fenced_code
try:
    from html.parser import HTMLParser
except ImportError as ex:
    from HTMLParser import HTMLParser
envget = os.environ.get

# ---------------------------------------------------------------------- Config
hr_sep, txt_block_cut, code_pref, list_pref, hr_ends = \
    '─', '✂', '| ', '- ', '◈'
# ansi cols (default):
# R: Red (warnings), L: low visi, BG: background, BGL: background light, C=code
# H1 - H5 = the theme, the numbers are the ansi color codes:
H1, H2, H3, H4, H5, R, L, BG, BGL, T, TL, C = \
    231, 153, 117, 109, 65, 124, 59, 16, 188, 188, 59, 102
# Code (C is fallback if we have no lexer). Default: Same theme:
CH1, CH2, CH3, CH4, CH5 = H1, H2, H3, H4, H5

code_hl = {"Keyword":   'CH3',
           "Name":      'CH1',
           "Comment":   'L',
           "String":    'CH4',
           "Error":     'R',
           "Number":    'CH4',
           "Operator":  'CH5',
           "Generic":   'CH2'
           }

admons = {'note':       'H3',
          'warning':    'R',
          'attention':  'H1',
          'hint':       'H4',
          'summary':    'H1',
          'hint':       'H4',
          'question':   'H5',
          'danger':     'R',
          'dev':        'H5',
          'hint':       'H4',
          'caution':    'H2'
          }

def_lexer = 'python'
guess_lexer = True
# also global. but not in use, BG handling can get pretty involved, to do with
# taste, since we don't know the term backg....:
background = BG

# hirarchical indentation by:
left_indent = '  '
# normal text color:
color = T

show_links = None

# columns(!) - may be set to smaller width:
# could be exported by the shell, normally not in subprocesses:
term_columns, term_rows = envget('COLUMNS'), envget('LINES')
if not term_columns:
    try:
        term_rows, term_columns = os.popen(
            'stty size 2>/dev/null', 'r').read().split()
        term_columns, term_rows = int(term_columns), int(term_rows)
    except:
        if '-' not in sys.argv:
            print('!! Could not derive your terminal width !!')
if not term_columns:
    term_columns = 80
if not term_rows:
    term_rows = 200
term_columns, term_rows = int(term_columns), int(term_rows)

# could be given, otherwise read from ansi_tables.json:
themes = {}


# sample for the theme roller feature:
md_sample = ''

# dir monitor recursion max:
mon_max_files = 1000
# ------------------------------------------------------------------ End Config

import logging
md_logger = logging.getLogger('MARKDOWN')
md_logger.setLevel(logging.WARNING)


# below here you have to *know* what u r doing... (since I didn't too much)

dir_mon_filepath_ph    = '_fp_'
dir_mon_content_raw    = '_raw_'
dir_mon_content_pretty = '_pretty_'


def read_themes():
    if not themes:
        with open(j(mydir, 'ansi_tables.json')) as f:
            themes.update(loads(f.read()))
    return themes


# can unescape:
html_parser = HTMLParser()
you_like = 'You like this theme?'


def make_sample():
    """ Generate the theme roller sample markdown """
    if md_sample:
        # user has set another:
        return md_sample
    _md = []
    for hl in range(1, 7):
        _md.append('#' * hl + ' ' + 'Header %s' % hl)
    this = open(__file__).read().split('"""', 3)[2].splitlines()[:10]
    _md.append('```\n""" Test """\n%s\n```' % '\n'.join(this).strip())
    _md.append("""
| Tables        | Fmt |
| -- | -- |
| !!! hint: wrapped | 0.1 **strong** |
    """)
    for ad in list(admons.keys())[:1]:
        _md.append('!!! %s: title\n    this is a %s\n' % (ad, ad.capitalize()))
    # 'this theme' replaced in the roller (but not at mdv w/o args):
    globals()['md_sample'] = \
        '\n'.join(_md) + '\n----\n!!! question: %s' % you_like


code_hl_tokens = {}


def build_hl_by_token():
    if not have_pygments:
        return
    # replace code strs with tokens:
    for k, col in list(code_hl.items()):
        code_hl_tokens[getattr(token, k)] = globals()[col]


def clean_ansi(s):
    # if someone does not want the color foo:
    ansi_escape = re.compile(r'\x1b[^m]*m')
    return ansi_escape.sub('', s)

# markers: tab is 09, omit that
code_start, code_end = '\x07', '\x08'
stng_start, stng_end = '\x16', '\x10'
emph_start, emph_end = '\x11', '\x12'
punctuationmark      = '\x13'
fenced_codemark      = '\x14'
hr_marker            = '\x15'


def j(p, f):
    return os.path.join(p, f)

mydir = os.path.realpath(__file__).rsplit(os.path.sep, 1)[0]


def set_theme(theme=None, for_code=None, theme_info=None):
    """ set md and code theme """
    # for md the default is None and should return the 'random' theme
    # for code the default is 'default' and should return the default theme.
    # historical reasons...
    dec = {False: {'dflt': None, 'on_dflt': 'random',
                   'env': ('MDV_THEME', 'AXC_THEME')},
           True: {'dflt': 'default', 'on_dflt': None,
                  'env': ('MDV_CODE_THEME', 'AXC_CODE_THEME')}}
    dec = dec[bool(for_code)]
    try:
        if theme == dec['dflt']:
            for k in dec['env']:
                ek = envget(k)
                if ek:
                    theme = ek
                    break
        if theme == dec['dflt']:
            theme = dec['on_dflt']
        if not theme:
            return

        theme = str(theme)
        # all the themes from here:
        themes = read_themes()
        if theme == 'random':
            rand = randint(0, len(themes) - 1)
            theme = list(themes.keys())[rand]
        t = themes.get(theme)
        if not t or len(t.get('ct')) != 5:
            # leave defaults:
            return
        _for = ''
        if for_code:
            _for = ' (code)'

        if theme_info:
            print(low('theme%s: %s (%s)' % (_for, theme, t.get('name'))))

        t = t['ct']
        cols = (t[0], t[1], t[2], t[3], t[4])
        if for_code:
            global CH1, CH2, CH3, CH4, CH5
            CH1, CH2, CH3, CH4, CH5 = cols
        else:
            global H1, H2, H3, H4, H5
            # set the colors now from the ansi codes in the theme:
            H1, H2, H3, H4, H5 = cols
    finally:
        if for_code:
            build_hl_by_token()


def style_ansi(raw_code, lang=None):
    """ actual code hilite """
    lexer = 0
    if lang:
        try:
            lexer = get_lexer_by_name(lang)
        except ValueError:
            print(col(R, 'Lexer for %s not found' % lang))

    if not lexer:
        try:
            if guess_lexer:
                lexer = pyg_guess_lexer(raw_code)
        except:
            pass
    if not lexer:
        lexer = get_lexer_by_name(def_lexer)
    tokens = lex(raw_code, lexer)
    cod = []
    for t, v in tokens:
        if not v:
            continue
        _col = code_hl_tokens.get(t)  # color
        if _col:
            cod.append(col(v, _col))
        else:
            cod.append(v)
    return ''.join(cod)


def col_bg(c):
    """ colorize background """
    return '\033[48;5;%sm' % c


def col(s, c, bg=0, no_reset=0):
    """
    print col('foo', 124) -> red 'foo' on the terminal
    c = color, s the value to colorize """
    reset = reset_col
    if no_reset:
        reset = ''
    for _strt, _end, _col in ((code_start, code_end, H2),
                              (stng_start, stng_end, H2),
                              (emph_start, emph_end, H3)):
        if _strt in s:
            # inline code:
            s = s.replace(_strt, col('', _col, bg=background, no_reset=1))
            s = s.replace(_end,  col('', c, no_reset=1))

    s = '\033[38;5;%sm%s%s' % (c, s, reset)
    if bg:
        pass
        # s = col_bg(bg) + s
    return s

reset_col = '\033[0m'


def low(s):
    # shorthand
    return col(s, L)


def plain(s, **kw):
    # when a tag is not found:
    return col(s, T)


def sh(out):
    ''' debug tool'''
    for l in out:
        print(l)


# --------------------------------------------------------- Tag formatter funcs
class Tags:
    """ can be overwritten in derivations. """
    # @staticmethod everywhere is eye cancer, so we instantiate it later
    def h(_, s, level):
        return '\n%s%s' % (low('#' * 0), col(s, globals()['H%s' % level]))
    def h1(_, s, **kw): return _.h(s, 1)
    def h2(_, s, **kw): return _.h(s, 2)
    def h3(_, s, **kw): return _.h(s, 3)
    def h4(_, s, **kw): return _.h(s, 4)
    def h5(_, s, **kw): return _.h(s, 5)
    def h6(_, s, **kw): return _.h(s, 5)  # have not more then 5
    def h7(_, s, **kw): return _.h(s, 5)  # cols in the themes, low them all
    def h8(_, s, **kw): return _.h(s, 5)

    def p (_, s, **kw): return col(s, T)
    def a (_, s, **kw): return col(s, L)
    def hr(_, s, **kw):
        # we want nice line seps:
        hir = kw.get('hir', 1)
        ind = (hir - 1) * left_indent
        s = e = col(hr_ends, globals()['H%s' % hir])
        return low('\n%s%s%s%s%s\n' % (ind, s, hr_marker, e, ind))

    def code(_, s, from_fenced_block=None, **kw):
        """ md code AND ``` style fenced raw code ends here"""
        lang = kw.get('lang')

        # funny: ":-" confuses the tokenizer. replace/backreplace:
        raw_code = s.replace(':-', '\x01--')
        if have_pygments:
            s = style_ansi(raw_code, lang=lang)

        # outest hir is 2, use it for fenced:
        ind = ' ' * kw.get('hir', 2)
        # if from_fenced_block: ... WE treat equal.

        # shift to the far left, no matter the indent (screenspace matters):
        firstl = s.split('\n')[0]
        del_spaces = ' ' * (len(firstl) - len(firstl.lstrip()))
        s = ('\n' + s).replace('\n%s' % del_spaces, '\n')[1:]

        # we want an indent of one and low vis prefix. this does it:
        code_lines = ('\n' + s).splitlines()
        prefix = ('\n%s%s %s' % (ind, low(code_pref), col('', C, no_reset=1)))
        code = prefix.join(code_lines)
        code = code.replace('\x01--', ':-')
        return code + '\n' + reset_col


def is_text_node(el):
    """ """
    s = str(etree.tostring(el))
    # strip our tag:
    html = s.split('<%s' % el.tag, 1)[1].split('>', 1)[1].rsplit('>', 1)[0]
    # do we start with another tagged child which is NOT in inlines:?
    if not html.startswith('<'):
        return 1, html
    for inline in ('<em>', '<code>', '<strong>'):
        if html.startswith(inline):
            return 1, html
    return 0, 0


# ----------------------------------------------------- Text Termcols Adaptions
def rewrap(el, t, ind, pref):
    """ Reasonably smart rewrapping checking punctuations """
    global term_columns
    cols = max(term_columns - len(ind + pref), 5)
    if el.tag == 'code' or len(t) <= cols:
        return t

    # this is a code replacement marker of markdown.py. Don't split the
    # replacement marker:
    if t.startswith('\x02') and t.endswith('\x03'):
        return t

    dedented = textwrap.dedent(t).strip()
    ret = textwrap.fill(dedented, width=cols)
    return ret

    # forgot why I didn't use textwrap from the beginning. In case there is a
    # reason I leave the old code here:
    # wrapping:
    # we want to keep existing linebreaks after punctuation
    # marks. the others we rewrap:

    puncs = ',', '.', '?', '!', '-', ':'
    parts = []
    origp = t.splitlines()
    if len(origp) > 1:
        pos = -1
        while pos < len(origp) - 1:
            pos += 1
            # last char punctuation?
            if origp[pos][-1] not in puncs and \
                    not pos == len(origp) - 1:
                # concat:
                parts.append(origp[pos].strip() + ' ' + origp[pos + 1].strip())
                pos += 1
            else:
                parts.append(origp[pos].strip())
        t = '\n'.join(parts)
    # having only the linebreaks with puncs before we rewrap
    # now:
    parts = []
    for part in t.splitlines():
        parts.extend([part[i:i+cols] for i in range(0, len(part), cols)])
    # last remove leading ' ' (if '\n' came just before):
    t = []
    for p in parts:
        t.append(p.strip())
    return '\n'.join(t)


def split_blocks(text_block, w, cols, part_fmter=None):
    """ splits while multiline blocks vertically (for large tables) """
    ts = []
    for line in text_block.splitlines():
        parts = []
        # make equal len:
        line = line.ljust(w, ' ')
        # first part full width, others a bit indented:
        parts.append(line[:cols])
        scols = cols - 2
        # the txt_block_cut in low makes the whole secondary tables
        # low. which i find a feature:
        # if you don't want it remove the col(.., L)
        parts.extend([' ' + col(txt_block_cut, L, no_reset=1) +
                      line[i:i + scols]
                      for i in range(cols, len(line), scols)])
        ts.append(parts)

    blocks = []
    for block_part_nr in range(len(ts[0])):
        tpart = []
        for lines_block in ts:
            tpart.append(lines_block[block_part_nr])
        if part_fmter:
            part_fmter(tpart)
        tpart[1] = col(tpart[1], H3)
        blocks.append('\n'.join(tpart))
    t = '\n'.join(blocks)
    return('\n%s\n' % t)


# ---------------------------------------------------- Create the treeprocessor
class AnsiPrinter(Treeprocessor):
    header_tags = ('h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'h7', 'h8')

    def run(self, doc):
        tags = Tags()

        def get_attr(el, attr):
            for c in list(el.items()):
                if c[0] == attr:
                    return c[1]
            return ''

        def formatter(el, out, hir=0, pref='', parent=None):
            """
            debugging:
            # if el.tag == 'div':
            # for c in el.getchildren()[3].getchildren(): print c.text, c
            """
            is_txt_and_inline_markup = 0

            if el.tag == 'hr':
                return out.append(tags.hr('', hir=hir))

            if el.text or                       \
               el.tag == 'p' or                 \
               el.tag == 'li' or                \
               el.tag.startswith('h'):

                el.text = el.text or ''
                # <a attributes>foo... -> we want "foo....". Is it a sub
                # tag or inline text?
                is_txt_and_inline_markup, html = is_text_node(el)

                if is_txt_and_inline_markup:
                    t = html.rsplit('<', 1)[0]
                    for tg, start, end in (('code',   code_start, code_end),
                                           ('strong', stng_start, stng_end),
                                           ('em',     emph_start, emph_end)):
                        t = t.replace('<%s>' % tg,  start)
                        t = t.replace('</%s>' % tg, end)
                    t = html_parser.unescape(t)
                else:
                    t = el.text

                t = t.strip()
                admon = ''
                pref = body_pref = ''
                if t.startswith('!!! '):
                    # we allow admons with spaces. so check for startswith:
                    _ad = None
                    for k in admons:
                        if t[4:].startswith(k):
                            _ad = k
                            break
                    # not found - markup using hte first one's color:
                    if not _ad:
                        k = t[4:].split(' ', 1)[0]
                        admons[k] = admons.values()[0]

                    pref = body_pref = '┃ '
                    pref += (k.capitalize())
                    admon = k
                    t = t.split(k, 1)[1]

                # set the parent, e.g. nrs in ols:
                if el.get('pref'):
                    # first line pref, like '-':
                    pref = el.get('pref')
                    # next line prefs:
                    body_pref = ' ' * len(pref)
                    el.set('pref', '')

                ind = left_indent * hir
                if el.tag in self.header_tags:
                    # header level:
                    hl = int(el.tag[1:])
                    ind = ' ' * (hl - 1)
                    hir += hl

                t = rewrap(el, t, ind, pref)

                # indent. can color the prefixes now, no more len checks:
                if admon:
                    out.append('\n')
                    pref      = col(pref,      globals()[admons[admon]])
                    body_pref = col(body_pref, globals()[admons[admon]])

                if pref:
                    # different color per indent:
                    h = globals()['H%s' % (((hir - 2) % 5) + 1)]
                    if pref == list_pref:
                        pref = col(pref, h)
                    elif pref.split('.', 1)[0].isdigit():
                        pref = col(pref, h)

                t = ('\n' + ind + body_pref).join((t).splitlines())
                t = ind + pref + t

                # headers outer left: go sure.
                # actually... NO. commented out.
                # if el.tag in self.header_tags:
                #    pref = ''

                # calling the class Tags  functions
                # IF the parent is li and we have a linebreak then the renderer
                # delivers <li><p>foo</p> instead of <li>foo, i.e. we have to
                # omit the linebreak and append the text of p to the previous
                # result, (i.e. the list separator):
                tag_fmt_func = getattr(tags, el.tag, plain)
                if type(parent) == type(el) and parent.tag == 'li' and \
                        not parent.text and el.tag == 'p':
                    _out = tag_fmt_func(t.lstrip(), hir=hir)
                    out[-1] += _out
                else:
                    out.append(tag_fmt_func(t, hir=hir))
                if show_links:
                    for l in 'src', 'href':
                        if l in list(el.keys()):
                            out[-1] += low('(%s) ' % get_attr(el, l))

                if admon:
                    out.append('\n')

            # have children?
            #    nr for ols:
            if is_txt_and_inline_markup:
                if el.tag == 'li':
                    childs = el.getchildren()
                    for nested in 'ul', 'ol':
                        if childs and childs[-1].tag == nested:
                            ul = childs[-1]
                            # do we have a nested sublist? the li was inline
                            # formattet,
                            # split all from <ul> off and format it as own tag:
                            # (ul always at the end of an li)
                            out[-1] = out[-1].split('<%s>' % nested, 1)[0]
                            formatter(ul, out, hir + 1, parent=el)
                return

            if el.tag == 'table':
                # processed all here, in one sweep:
                # markdown ext gave us a xml tree from the ascii,
                # our part here is the cell formatting and into a
                # python nested list, then tabulate spits
                # out ascii again:
                def borders(t):
                    t[0] = t[-1] = low(t[0].replace('-', '─'))

                def fmt(cell, parent):
                    """ we just run the whole formatter - just with a fresh new
                    result list so that our 'out' is untouched """
                    _cell = []
                    formatter(cell, out=_cell, hir=0, parent=parent)
                    return '\n'.join(_cell)

                t = []
                for he_bo in 0, 1:
                    for Row in el[he_bo].getchildren():
                        row = []
                        t.append(row)
                        for cell in Row.getchildren():
                            row.append(fmt(cell, row))
                cols = term_columns
                # good ansi handling:
                tbl = tabulate(t)

                # do we have right room to indent it?
                # first line is seps, so no ansi esacapes foo:
                w = len(tbl.split('\n', 1)[0])
                if w <= cols:
                    t = tbl.splitlines()
                    borders(t)
                    # center:
                    ind = (cols - w) / 2
                    # too much:
                    ind = hir
                    tt = []
                    for line in t:
                        tt.append('%s%s' % (ind * left_indent, line))
                    out.extend(tt)
                else:
                    # TABLE CUTTING WHEN NOT WIDTH FIT
                    # oh snap, the table bigger than our screen. hmm.
                    # hey lets split into vertical parts:
                    # but len calcs are hart, since we are crammed with esc.
                    # seqs.
                    # -> get rid of them:
                    tc = []
                    for row in t:
                        tc.append([])
                        l = tc[-1]
                        for cell in row:
                            l.append(clean_ansi(cell))
                    # again sam:
                    # note: we had to patch it, it inserted '\n' within cells!
                    table = tabulate(tc)
                    out.append(
                        split_blocks(table, w, cols, part_fmter=borders))
                return

            nr = 0
            for c in el:
                if el.tag == 'ul':  # or el.tag == 'li':
                    c.set('pref', list_pref)
                elif el.tag == 'ol':
                    nr += 1
                    c.set('pref', str(nr) + '. ')

                # handle the ``` style unindented code blocks -> parsed as p:
                formatter(c, out, hir + 1, parent=el)
            # if el.tag == 'ul' or el.tag == 'ol' and not out[-1] == '\n':
            #    out.append('\n')

        out = []
        formatter(doc, out)
        self.markdown.ansi = '\n'.join(out)


def set_hr_widths(result):
    """
    We want the hrs indented by hirarchy...
    A bit 2 much effort to calc, maybe just fixed with 10
    style seps would have been enough visually:
    ◈────────────◈
    """
    # set all hrs to max width of text:
    mw = 0
    hrs = []
    if hr_marker not in result:
        return result
    for line in result.splitlines():
        if hr_marker in line:
            hrs.append(line)
            continue
        if len(line) < mw:
            continue
        l = len(clean_ansi(line))
        if l > mw:
            mw = l
    for hr in hrs:
        # pos of hr marker is indent, derives full width:
        # (more indent = less '-'):
        hcl = clean_ansi(hr)
        ind = len(hcl) - len(hcl.split(hr_marker, 1)[1]) - 1
        w = min(term_columns, mw) - 2 * ind
        hrf = hr.replace(hr_marker, hr_sep * w)
        result = result.replace(hr, hrf)
    return result


# Then tell markdown about it
class AnsiPrintExtension(Extension):
    def extendMarkdown(self, md, md_globals):
        ansi_print_ext = AnsiPrinter(md)
        md.treeprocessors.add('ansi_print_ext', ansi_print_ext, '>inline')


def do_code_hilite(md, what='all'):
    '''
    "inverse" mode for source code highlighting:
    the file contains mainly code and md is within docstrings
    what in  all, code, doc, mod
    '''
    if what not in ('all', 'code', 'doc', 'mod'):
        what = 'all'
    code_mode, md_mode = 1, 2
    blocks, block, mode = [], [], code_mode
    blocks.append([mode, block])
    lines = ('\n' + md).splitlines()
    mdstart = '\x01'
    while lines:
        line = lines.pop(0)
        if mode == code_mode:
            if line.rstrip() in ('"""_', "'''_", "/*_"):
                mdstart = line.rstrip()[:-1]
                mode = md_mode
                block = []
                if mdstart == '/*':
                    mdstart = '*/'
                blocks.append([md_mode, block])
                continue

        elif line.rstrip() == mdstart:
            if what == 'doc':
                # only module level docstring:
                break
            mode = code_mode
            block = []
            blocks.append([code_mode, block])
            continue

        if mode == code_mode:
            if what in ('all', 'code'):
                block.append(line)

        elif what != 'code':
            block.append(line)

    out = []
    for mode, block in blocks:
        b = '\n'.join(block)
        if not b:
            continue
        if mode == code_mode:
            out.append('```\n%s\n```' % b)
        else:
            out.append('\n'.join(block))
    return '\n'.join(out)


def main(md=None, filename=None, cols=None, theme=None, c_theme=None, bg=None,
         c_no_guess=None, display_links=None, from_txt=None, do_html=None,
         code_hilite=None, c_def_lexer=None,
         theme_info=None, no_colors=None, tab_length=4, **kw):
    """ md is markdown string. alternatively we use filename and read """
    tab_length = tab_length or 4
    global def_lexer
    if c_def_lexer:
        def_lexer = c_def_lexer
    py_config_file = os.path.expanduser("~/.mdv.py")
    if os.path.exists(py_config_file):
        exec_globals = {}
        exec(io.open(py_config_file, encoding='utf-8').read(), exec_globals)
        globals().update(exec_globals)

    args = locals()
    if not md:
        if not filename:
            print('Using sample markdown:')
            make_sample()
            md = args['md'] = md_sample
            print(md)
            print
            print('Styling Result')
        else:
            if filename == '-':
                md = sys.stdin.read()
            else:
                with open(filename) as f:
                    md = f.read()

    global term_columns
    # style rolers requested?
    if c_theme == 'all' or theme == 'all':
        if c_theme == 'all':
            os.environ['AXC_CODE_THEME'] = os.environ['MDV_CODE_THEME'] = ''
        if theme == 'all':
            os.environ['AXC_THEME'] = os.environ['MDV_THEME'] = ''
        args.pop('kw')
        themes = read_themes()
        for k, v in list(themes.items()):
            if not filename:
                yl = 'You like *%s*, *%s*?' % (k, v['name'])
                args['md'] = md_sample.replace(you_like, yl)
            print(col('%s%s%s' % ('\n\n', '=' * term_columns, '\n'), L))
            # should really create an iterator here:
            if theme == 'all':
                args['theme'] = k
            else:
                args['c_theme'] = k
            print(main(**args))
        return ''

    if cols:
        term_columns = int(cols)

    global show_links
    show_links = display_links

    if bg and bg == 'light':
        # not in use rite now:
        global background, color
        background = BGL
        color = T

    set_theme(theme, theme_info=theme_info)

    global guess_lexer
    guess_lexer = not c_no_guess

    if not c_theme:
        c_theme = theme or 'default'

    if c_theme == 'None':
        c_theme = None

    if c_theme:
        set_theme(c_theme, for_code=1, theme_info=theme_info)

    if c_theme:
        # info:
        if not have_pygments:
            print(col('No pygments, can not analyze code for hilite', R))

    # Create an instance of the Markdown class with the new extension
    MD = markdown.Markdown(tab_length=int(tab_length),
                           extensions=[AnsiPrintExtension(),
                                       TableExtension(),
                                       fenced_code.FencedCodeExtension()])
    if code_hilite:
        md = do_code_hilite(md, code_hilite)
    the_html = MD.convert(md)
    # html?
    if do_html:
        return the_html

    # who wants html, here is our result:
    try:
        ansi = MD.ansi
    except:
        if the_html:
            # can this happen? At least show:
            print("we have markdown result but no ansi.")
            print(the_html)
        else:
            ansi = 'n.a. (no parsing result)'

    # The RAW html within source, incl. fenced code blocks:
    # phs are numbered like this in the md, we replace back:
    PH = markdown.util.HTML_PLACEHOLDER
    stash = MD.htmlStash
    nr = -1
    tags = Tags()
    for ph in stash.rawHtmlBlocks:
        nr += 1
        raw = html_parser.unescape(ph[0])
        pre = '<pre><code'
        if raw.startswith(pre):
            pre, raw = raw.split(pre, 1)
            raw = raw.split('>', 1)[1].rsplit('</code>', 1)[0]
            if 'class="' in pre:
                # language:
                lang = pre.split('class="', 1)[1].split('"')[0]
            else:
                lang = ''
            raw = tags.code(raw.strip(), from_fenced_block=1, lang=lang)
        ansi = ansi.replace(PH % nr, raw)

    # don't want these: gone through the extension now:
    # ansi = ansi.replace('```', '')

    # sub part display (the -f feature)
    if from_txt:
        if not from_txt.split(':', 1)[0] in ansi:
            # display from top then:
            from_txt = ansi.strip()[1]
        from_txt, mon_lines = (
            from_txt + ':%s' % (term_rows - 6)).split(':')[:2]
        mon_lines = int(mon_lines)
        pre, post = ansi.split(from_txt, 1)
        post = '\n'.join(post.split('\n')[:mon_lines])
        ansi = '\n(...)%s%s%s' % (
               '\n'.join(pre.rsplit('\n', 2)[-2:]), from_txt, post)

    ansi = set_hr_widths(ansi) + '\n'
    if no_colors:
        return clean_ansi(ansi)
    return ansi + '\n'


# Following just file monitors, not really core feature so the prettyfier:
# but sometimes good to have at hand:
# ---------------------------------------------------------------- File Monitor
def monitor(args):
    """ file monitor mode """
    filename = args.get('MDFILE')
    if not filename:
        print(col('Need file argument', 2))
        raise SystemExit
    last_err = ''
    last_stat = 0
    while True:
        if not os.path.exists(filename):
            last_err = 'File %s not found. Will continue trying.' % filename
        else:
            try:
                stat = os.stat(filename)[8]
                if stat != last_stat:
                    parsed = run_args(args)
                    print(str(parsed))
                    last_stat = stat
                last_err = ''
            except Exception as ex:
                last_err = str(ex)
        if last_err:
            print('Error: %s' % last_err)
        sleep()


def sleep():
    try:
        time.sleep(1)
    except KeyboardInterrupt:
        print('Have a nice day!')
        raise SystemExit


# ----------------------------------------------------------- Directory Monitor
def run_changed_file_cmd(cmd, fp, pretty):
    """ running commands on changes.
        pretty the parsed file
    """
    with open(fp) as f:
        raw = f.read()
    # go sure regarding quotes:
    for ph in (dir_mon_filepath_ph, dir_mon_content_raw,
               dir_mon_content_pretty):
        if ph in cmd and \
                not ('"%s"' % ph) in cmd and \
                not ("'%s'" % ph) in cmd:
            cmd = cmd.replace(ph, '"%s"' % ph)

    cmd = cmd.replace(dir_mon_filepath_ph, fp)
    print(col('Running %s' % cmd, H1))
    for r, what in ((dir_mon_content_raw, raw),
                    (dir_mon_content_pretty, pretty)):
        cmd = cmd.replace(r, what.encode('base64'))

    # yeah, i know, sub bla bla...
    if os.system(cmd):
        print(col('(the command failed)', R))


def monitor_dir(args):
    """ displaying the changed files """

    def show_fp(fp):
        args['MDFILE'] = fp
        pretty = run_args(args)
        print(pretty)
        print("(%s)" % col(fp, L))
        cmd = args.get('change_cmd')
        if cmd:
            run_changed_file_cmd(cmd, fp=fp, pretty=pretty)

    ftree = {}
    d = args.get('-M')
    # was a change command given?
    d += '::'
    d, args['change_cmd'] = d.split('::')[:2]
    args.pop('-M')
    # collides:
    args.pop('-m')
    d, exts = (d + ':md,mdown,markdown').split(':')[:2]
    exts = exts.split(',')
    if not os.path.exists(d):
        print(col('Does not exist: %s' % d, R))
        sys.exit(2)

    dir_black_list = ['.', '..']

    def check_dir(d, ftree):
        check_latest = ftree.get('latest_ts')
        d = os.path.abspath(d)
        if d in dir_black_list:
            return

        if len(ftree) > mon_max_files:
            # too deep:
            print(col('Max files (%s) reached' % col(mon_max_files, R)))
            dir_black_list.append(d)
            return
        try:
            files = os.listdir(d)
        except Exception as ex:
            print('%s when scanning dir %s' % (col(ex, R), d))
            dir_black_list.append(d)
            return

        for f in files:
            fp = j(d, f)
            if os.path.isfile(fp):
                f_ext = f.rsplit('.', 1)[-1]
                if f_ext == f:
                    f_ext == ''
                if f_ext not in exts:
                    continue
                old = ftree.get(fp)
                # size:
                now = os.stat(fp)[6]
                if check_latest:
                    if os.stat(fp)[7] > ftree['latest_ts']:
                        ftree['latest'] = fp
                        ftree['latest_ts'] = os.stat(fp)[8]
                if now == old:
                    continue
                # change:
                ftree[fp] = now
                if not old:
                    # At first time we don't display ALL the files...:
                    continue
                # no binary. make sure:
                if 'text' in os.popen('file "%s"' % fp).read():
                    show_fp(fp)
            elif os.path.isdir(fp):
                check_dir(j(d, fp), ftree)

    ftree['latest_ts'] = 1
    while True:
        check_dir(d, ftree)
        if 'latest_ts' in ftree:
            ftree.pop('latest_ts')
            fp = ftree.get('latest')
            if fp:
                show_fp(fp)
            else:
                print('sth went wrong, no file found')
        sleep()


def load_yaml_config():
    import yaml
    cfg = yaml.load('{}')
    config_file = os.path.expanduser("~/.mdv")
    if os.path.exists(config_file):
        with open(config_file, "r") as mdvconfig:
            try:
                cfg = yaml.load(mdvconfig)
            except IOError:
                pass
    return cfg


def merge(a, b):
    c = a.copy()
    c.update(b)
    return c


def run_args(args):
    """ call the lib entry function with CLI args """
    return main(filename      = args.get('MDFILE'),
                theme         = args.get('-t', 'random'),
                cols          = args.get('-c'),
                from_txt      = args.get('-f'),
                code_hilite   = args.get('-C'),
                c_theme       = args.get('-T'),
                c_no_guess    = args.get('-x'),
                c_def_lexer   = args.get('-X'),
                do_html       = args.get('-H'),
                theme_info    = args.get('-i'),
                no_colors     = args.get('-A'),
                display_links = args.get('-L'),
                tab_length    = args.get('-b', 4))


def run():
    global is_app
    is_app = 1
    if sys.version_info[0] == 2:
        # Make Py2 > Py3:
        import imp
        imp.reload(sys)
        sys.setdefaultencoding('utf-8')
        # no? see http://stackoverflow.com/a/29832646/4583360 ...

    doc = __doc__[1:]
    if '-h' in sys.argv or '--help' in sys.argv:
        print(main(md=doc, c_no_guess=1, theme=909.0365, c_theme=579.6579,
              c_def_lexer='yaml'))
        sys.exit(0)

    # our docstring markdown to docopt:
    d = doc.split('# Notes', 1)[0]    \
        .replace('\n\n', '\n')        \
        .replace('\n# ', '\n\n')      \
        .strip()
    args = docopt(d, version='mdv v0.1')
    args = merge(args, load_yaml_config())
    if args.get('-m'):
        monitor(args)
    if args.get('-M'):
        monitor_dir(args)
    else:
        if sys.version_info[0] == 2:
            print(str(run_args(args)))
        else:
            print(run_args(args))

if __name__ == '__main__':
    # the setup tools version calls directly run, this is for git checkouts:
    run()
