#!/usr/bin/env python

from __future__ import with_statement
from optparse import OptionParser
from string import Template
import cgi
import cStringIO
import os
import os.path
import re
import sys


EXIT_CODE_SHOW_HTML = 205
EXIT_CODE_SHOW_TOOLTIP = 206


def exit_show_html(html):
    sys.stdout.write(html.encode('utf-8'))
    sys.exit(EXIT_CODE_SHOW_HTML)


def exit_show_tooltip(text):
    sys.stdout.write(text)
    sys.exit(EXIT_CODE_SHOW_TOOLTIP)


def within_textmate():
    return os.getenv('TM_APP_PATH') is not None


def tabs2spaces(text, positions=None):
    while True:
        index = text.find(u'\t')

        if index < 0:
            return text

        spaces = u' ' * (4 - (index % 4))
        text = text[0:index] + spaces + text[index + 1:]

        if positions is not None:
            positions.append(index)


def relative_path(basedir, filename):
    if filename.find(basedir) == 0:
        filename = filename[len(basedir) + 1:]

    return filename


class LintChecker(object):

    VAR_BLOCK_START_RE = re.compile(ur'''(?x)
        (?P<indent>\s*)         # indent before a var keyword
        (?P<var>var\s+)         # var keyword and whitespace after
        (?P<identifier>[a-zA-Z_$]\w*)\s*
        (?:
            (?P<assignment>=)\s*
            (?P<expression>.*)
            |
            (?P<separator>[,;+\-/*%^&|=\\])
        )
    ''')

    SEPARATOR_RE = re.compile(ur'''(?x)
        (?P<expression>.*)              # Everything up to the line separator
        (?P<separator>[,;+\-/*%^&|=\\]) # The line separator
        \s*                             # Optional whitespace after
        $                               # End of expression
    ''')

    INDENTED_EXPRESSION_RE_TEMPLATE = ur'''(?x)
        [ ]{%d}                 # Placeholder for indent of first identifier that started block
        (?P<expression>.+)      # Expression
    '''

    VAR_BLOCK_RE_TEMPLATE = ur'''(?x)
        [ ]{%d}                 # Placeholder for indent of first identifier that started block
        (?P<indent>\s*)         # Capture any further indent
        (?:
            (?P<bracket>[\[\{].*)
            |
            (?P<identifier>[a-zA-Z_$]\w*)\s*
            (?:
                (?P<assignment>=)\s*
                (?P<expression>.*)
                |
                (?P<separator>[,;+\-/*%%^&|=\\])
            )
            |
            (?P<indented_expression>.+)
        )
    '''

    STATEMENT_RE = re.compile(ur'''(?x)
        \s*((continue|do|for|function|if|else|return|switch|while|with)\b|\[+\s*[a-zA-Z_$]\w*\s+[a-zA-Z_$]\w*\s*[:\]])
    ''')

    TRAILING_WHITESPACE_RE = re.compile(ur'^.*(\s+)$')
    STRIP_LINE_COMMENT_RE = re.compile(ur'(.*)\s*(?://.*|/\*.*\*/\s*)$')
    LINE_COMMENT_RE = re.compile(ur'\s*(?:/\*.*\*/\s*|//.*)$')
    BLOCK_COMMENT_START_RE = re.compile(ur'\s*/\*.*(?!\*/\s*)$')
    BLOCK_COMMENT_END_RE = re.compile(ur'.*?\*/')
    METHOD_RE = ur'[-+]\s*\([a-zA-Z_$]\w*\)\s*[a-zA-Z_$]\w*'
    FUNCTION_RE = re.compile(ur'\s*function\s*(?P<name>[a-zA-Z_$]\w*)?\(.*\)\s*\{?')
    STRING_LITERAL_RE = re.compile(ur'(?<!\\)(["\'])(.*?)(?<!\\)\1')
    EMPTY_STRING_LITERAL_FUNCTION = lambda match: match.group(1) + (len(match.group(2)) * ' ') + match.group(1)
    EMPTY_SELF_STRING_LITERAL_FUNCTION = lambda self, match: match.group(1) + (len(match.group(2)) * ' ') + match.group(1)

    ERROR_TYPE_ILLEGAL = 1
    ERROR_TYPE_WARNING = 2

    LINE_CHECKLIST = (
        {
            'id': 'tabs',
            'regex': re.compile(ur'[\t]'),
            'error': 'line contains tabs',
            'type': ERROR_TYPE_ILLEGAL
        },
        {
            'regex': re.compile(ur'([^\t -~])'),
            'error': 'line contains non-ASCII characters',
            'showPositionForGroup': 1,
            'type': ERROR_TYPE_ILLEGAL
        },
        {
            'regex': re.compile(ur'^\s*(?:(?:else )?if|for|switch|while|with)(\()'),
            'error': 'missing space between control statement and parentheses',
            'showPositionForGroup': 1,
            'type': ERROR_TYPE_WARNING
        },
        {
            'regex': re.compile(ur'^\s*(?:(?:else )?if|for|switch|while|with)\s*\(.+\)\s*(\{)\s*(?://.*|/\*.*\*/\s*)?$'),
            'error': 'braces should be on their own line',
            'showPositionForGroup': 1,
            'type': ERROR_TYPE_ILLEGAL
        },
        {
            'regex': TRAILING_WHITESPACE_RE,
            'error': 'trailing whitespace',
            'showPositionForGroup': 1,
            'type': ERROR_TYPE_ILLEGAL
        },
        {
            # Filter out @import statements, method declarations, method parameters, unary plus/minus/increment/decrement
            'filter': {'regex': re.compile(ur'(^@import\b|^\s*' + METHOD_RE + '|^\s*[a-zA-Z_$]\w*:\s*\([a-zA-Z_$][\w<>]*\)\s*\w+|[a-zA-Z_$]\w*(\+\+|--)|([ -+*/%^&|<>!]=?|&&|\|\||<<|>>>|={1,3}|!==?)\s*[-+][\w(\[])'), 'pass': False},

            # Replace the contents of literal strings with spaces so we don't get false matches within them
            'preprocess': (
                {'regex': STRIP_LINE_COMMENT_RE, 'replace': ''},
                {'regex': STRING_LITERAL_RE, 'replace': EMPTY_STRING_LITERAL_FUNCTION},
            ),
            'regex':      re.compile(ur'(?<=[\w)\]"\']|([ ]))([-+*/%^]|&&?|\|\|?|<<|>>>?)(?=[\w({\["\']|(?(1)\b\b|[ ]))'),
            'error':      'binary operator without surrounding spaces',
            'showPositionForGroup': 2,
            'type': ERROR_TYPE_WARNING
        },
        {
            # Filter out @import statements, method declarations
            'filter': {'regex': re.compile(ur'^(@import\b|\s*' + METHOD_RE + ')'), 'pass': False},

            # Replace the contents of literal strings with spaces so we don't get false matches within them
            'preprocess': (
                {'regex': STRIP_LINE_COMMENT_RE, 'replace': ''},
                {'regex': STRING_LITERAL_RE, 'replace': EMPTY_STRING_LITERAL_FUNCTION},
            ),
            'regex':      re.compile(ur'(?:[-*/%^&|<>!]=?|&&|\|\||<<|>>>|={1,3}|!==?)\s*(?<!\+)(\+)[\w(\[]'),
            'error':      'useless unary + operator',
            'showPositionForGroup': 1,
            'type': ERROR_TYPE_WARNING
        },
        {
            # Filter out possible = within @accessors
            'filter': {'regex': re.compile(ur'^\s*(?:@outlet\s+)?[a-zA-Z_$]\w*\s+[a-zA-Z_$]\w*\s+@accessors\b'), 'pass': False},

            # Replace the contents of literal strings with spaces so we don't get false matches within them
            'preprocess': (
                {'regex': STRIP_LINE_COMMENT_RE, 'replace': ''},
                {'regex': STRING_LITERAL_RE, 'replace': EMPTY_STRING_LITERAL_FUNCTION},
            ),
            'regex':      re.compile(ur'(?<=[\w)\]"\']|([ ]))(=|[-+*/%^&|]=|<<=|>>>?=)(?=[\w({\["\']|(?(1)\b\b|[ ]))'),
            'error':      'assignment operator without surrounding spaces',
            'showPositionForGroup': 2,
            'type': ERROR_TYPE_WARNING
        },
        {
            # Filter out @import statements and @implementation/method declarations
            'filter': {'regex': re.compile(ur'^(@import\b|@implementation\b|\s*' + METHOD_RE + ')'), 'pass': False},

            # Replace the contents of literal strings with spaces so we don't get false matches within them
            'preprocess': (
                {'regex': STRIP_LINE_COMMENT_RE, 'replace': ''},
                {'regex': STRING_LITERAL_RE, 'replace': EMPTY_STRING_LITERAL_FUNCTION},
            ),
            'regex':      re.compile(ur'(?<=[\w)\]"\']|([ ]))(===?|!==?|[<>]=?)(?=[\w({\["\']|(?(1)\b\b|[ ]))'),
            'error':      'comparison operator without surrounding spaces',
            'showPositionForGroup': 2,
            'type': ERROR_TYPE_WARNING
        },
        {
            'regex': re.compile(ur'^(\s+)' + METHOD_RE + '|^\s*[-+](\()[a-zA-Z_$][\w]*\)\s*[a-zA-Z_$]\w*|^\s*[-+]\s*\([a-zA-Z_$][\w]*\)(\s+)[a-zA-Z_$]\w*'),
            'error': 'extra or missing space in a method declaration',
            'showPositionForGroup': 0,
            'type': ERROR_TYPE_WARNING
        },
        {
            # Check for brace following a class or method declaration
            'regex': re.compile(ur'^(?:\s*[-+]\s*\([a-zA-Z_$]\w*\)|@implementation)\s*[a-zA-Z_$][\w]*.*?\s*(\{)\s*(?:$|//.*$)'),
            'error': 'braces should be on their own line',
            'showPositionForGroup': 0,
            'type': ERROR_TYPE_ILLEGAL
        },
        {
            'regex': re.compile(ur'^\s*var\s+[a-zA-Z_$]\w*\s*=\s*function\s+([a-zA-Z_$]\w*)\s*\('),
            'error': 'function name is ignored',
            'showPositionForGroup': 1,
            'skip': True,
            'type': ERROR_TYPE_WARNING
        },
    )

    VAR_DECLARATIONS = ['none', 'single', 'strict']
    VAR_DECLARATIONS_NONE = 0
    VAR_DECLARATIONS_SINGLE = 1
    VAR_DECLARATIONS_STRICT = 2

    DIRS_TO_SKIP = ('.git', 'Frameworks', 'Build', 'Resources', 'CommonJS', 'Objective-J')

    ERROR_FORMATS = ('text', 'html')
    TEXT_ERROR_SINGLE_FILE_TEMPLATE = Template(u'$lineNum: $message.\n+$line\n')
    TEXT_ERROR_MULTI_FILE_TEMPLATE = Template(u'$filename:$lineNum: $message.\n+$line\n')

    def __init__(self, basedir='', var_declarations=VAR_DECLARATIONS_SINGLE, verbose=False):
        self.basedir = unicode(basedir, 'utf-8')
        self.errors = []
        self.errorFiles = []
        self.filesToCheck = []
        self.varDeclarations = var_declarations
        self.verbose = verbose
        self.sourcefile = None
        self.filename = u''
        self.line = u''
        self.lineNum = 0
        self.varIndent = u''
        self.identifierIndent = u''

        self.fileChecklist = (
            {'title': 'Check variable blocks', 'action': self.check_var_blocks},
        )

    def run_line_checks(self):
        for check in self.LINE_CHECKLIST:
            line = self.line
            lineFilter = check.get('filter')

            if lineFilter:
                match = lineFilter['regex'].search(line)

                if (match and not lineFilter['pass']) or (not match and lineFilter['pass']):
                    continue

            preprocess = check.get('preprocess')

            if preprocess:
                if not isinstance(preprocess, (list, tuple)):
                    preprocess = (preprocess,)

                for processor in preprocess:
                    regex = processor.get('regex')

                    if regex:
                        line = regex.sub(processor.get('replace', ''), line)

            regex = check.get('regex')

            if not regex:
                continue

            match = regex.search(line)

            if not match:
                continue

            positions = []
            group = check.get('showPositionForGroup')

            if (check.get('id') == 'tabs'):
                line = tabs2spaces(line, positions=positions)
            elif group is not None:
                line = tabs2spaces(line)

                for match in regex.finditer(line):
                    if group > 0:
                        positions.append(match.start(group))
                    else:
                        # group 0 means show the first non-empty match
                        for i in range(1, len(match.groups()) + 1):
                            if match.start(i) >= 0:
                                positions.append(match.start(i))
                                break

            self.error(check['error'], line=line, positions=positions, type=check['type'])

    def next_statement(self, expect_line=False, check_line=True):
        try:
            while True:
                raw_line = self.sourcefile.next()[:-1]  # strip EOL

                try:
                    self.line = unicode(raw_line, 'utf-8', 'strict')  # convert to Unicode
                    self.lineNum += 1
                except UnicodeDecodeError:
                    self.line = unicode(raw_line, 'utf-8', 'replace')
                    self.lineNum += 1
                    self.error('line contains invalid unicode character(s)', type=self.ERROR_TYPE_ILLEGAL)

                if self.verbose:
                    print u'%d: %s' % (self.lineNum, tabs2spaces(self.line))

                if check_line:
                    self.run_line_checks()

                if not self.is_statement():
                    continue

                return True
        except StopIteration:
            if expect_line:
                self.error('unexpected EOF', type=self.ERROR_TYPE_ILLEGAL)
            raise

    def is_statement(self):
        # Skip empty lines
        if len(self.line.strip()) == 0:
            return False

        # See if we have a line comment, skip that
        match = self.LINE_COMMENT_RE.match(self.line)

        if match:
            return False

        # Match a block comment start next so we can find its end,
        # otherwise we might get false matches on the contents of the block comment.
        match = self.BLOCK_COMMENT_START_RE.match(self.line)

        if match:
            self.block_comment()
            return False

        return True

    def is_expression(self):
        match = self.STATEMENT_RE.match(self.line)
        return match is None

    def strip_comment(self):
        match = self.STRIP_LINE_COMMENT_RE.match(self.expression)

        if match:
            self.expression = match.group(1)

    def get_expression(self, lineMatch):
        groupdict = lineMatch.groupdict()

        self.expression = groupdict.get('expression')

        if self.expression is None:
            self.expression = groupdict.get('bracket')

        if self.expression is None:
            self.expression = groupdict.get('indented_expression')

        if self.expression is None:
            self.expression = ''
            return

        # Remove all quoted strings from the expression so that we don't
        # count unmatched pairs inside the strings.
        self.expression = self.STRING_LITERAL_RE.sub(self.EMPTY_SELF_STRING_LITERAL_FUNCTION, self.expression)

        self.strip_comment()
        self.expression = self.expression.strip()

    def block_comment(self):
        'Find the end of a block comment'

        commentOpenCount = self.line.count('/*')
        commentOpenCount -= self.line.count('*/')

        # If there is an open comment block, eat it
        if commentOpenCount:
            if self.verbose:
                print u'%d: BLOCK COMMENT START' % self.lineNum
        else:
            return

        match = None

        while not match and self.next_statement(expect_line=True, check_line=False):
            match = self.BLOCK_COMMENT_END_RE.match(self.line)

        if self.verbose:
            print u'%d: BLOCK COMMENT END' % self.lineNum

    def balance_pairs(self, squareOpenCount, curlyOpenCount, parenOpenCount):
        # The following lines have to be indented at least as much as the first identifier
        # after the var keyword at the start of the block.
        if self.verbose:
            print "%d: BALANCE BRACKETS: '['=%d, '{'=%d, '('=%d" % (self.lineNum, squareOpenCount, curlyOpenCount, parenOpenCount)

        lineRE = re.compile(self.INDENTED_EXPRESSION_RE_TEMPLATE % len(self.identifierIndent))

        while True:
            # If the expression has open brackets and is terminated, it's an error
            match = self.SEPARATOR_RE.match(self.expression)

            if match and match.group('separator') == ';':
                unterminated = []

                if squareOpenCount:
                    unterminated.append('[')

                if curlyOpenCount:
                    unterminated.append('{')

                if parenOpenCount:
                    unterminated.append('(')

                self.error('unbalanced %s' % ' and '.join(unterminated), type=self.ERROR_TYPE_ILLEGAL)
                return False

            self.next_statement(expect_line=True)
            match = lineRE.match(self.line)

            if not match:
                # If it doesn't match, the indent is wrong check the whole line
                self.error('incorrect indentation')
                self.expression = self.line
                self.strip_comment()
            else:
                # It matches, extract the expression
                self.get_expression(match)

            # Update the bracket counts
            squareOpenCount += self.expression.count('[')
            squareOpenCount -= self.expression.count(']')
            curlyOpenCount += self.expression.count('{')
            curlyOpenCount -= self.expression.count('}')
            parenOpenCount += self.expression.count('(')
            parenOpenCount -= self.expression.count(')')

            if squareOpenCount == 0 and curlyOpenCount == 0 and parenOpenCount == 0:
                if self.verbose:
                    print u'%d: BRACKETS BALANCED' % self.lineNum

                # The brackets are closed, this line must be separated
                match = self.SEPARATOR_RE.match(self.expression)

                if not match:
                    self.error('missing statement separator', type=self.ERROR_TYPE_ILLEGAL)
                    return False

                return True

    def pairs_balanced(self, lineMatchOrBlockMatch):

        groups = lineMatchOrBlockMatch.groupdict()

        if 'assignment' in groups or 'bracket' in groups:
            squareOpenCount = self.expression.count('[')
            squareOpenCount -= self.expression.count(']')

            curlyOpenCount = self.expression.count('{')
            curlyOpenCount -= self.expression.count('}')

            parenOpenCount = self.expression.count('(')
            parenOpenCount -= self.expression.count(')')

            if squareOpenCount or curlyOpenCount or parenOpenCount:
                # If the brackets were not properly closed or the statement was
                # missing a separator, skip the rest of the var block.
                if not self.balance_pairs(squareOpenCount, curlyOpenCount, parenOpenCount):
                    return False

        return True

    def var_block(self, blockMatch):
        """
        Parse a var block, return a tuple (haveLine, isSingleVar), where haveLine
        indicates whether self.line is the next line to be parsed.
        """

        # Keep track of whether this var block has multiple declarations
        isSingleVar = True

        # Keep track of the indent of the var keyword to compare with following lines
        self.varIndent = blockMatch.group('indent')

        # Keep track of how far the first variable name is indented to make sure
        # following lines line up with that
        self.identifierIndent = self.varIndent + blockMatch.group('var')

        # Check the expression to see if we have any open [ or { or /*
        self.get_expression(blockMatch)

        if not self.pairs_balanced(blockMatch):
            return (False, False)

        separator = ''

        if self.expression:
            match = self.SEPARATOR_RE.match(self.expression)

            if not match:
                self.error('missing statement separator', type=self.ERROR_TYPE_ILLEGAL)
            else:
                separator = match.group('separator')
        elif blockMatch.group('separator'):
            separator = blockMatch.group('separator')

        # If the block has a semicolon, there should be no more lines in the block
        blockHasSemicolon = separator == ';'

        # We may not catch an error till after the line that is wrong, so keep
        # the most recent declaration and its line number.
        lastBlockLine = self.line
        lastBlockLineNum = self.lineNum

        # Now construct an RE that will match any lines indented at least as much
        # as the var keyword that started the block.
        blockRE = re.compile(self.VAR_BLOCK_RE_TEMPLATE % len(self.identifierIndent))

        while self.next_statement(expect_line=not blockHasSemicolon):

            if not self.is_statement():
                continue

            # Is the line indented at least as much as the var keyword that started the block?
            match = blockRE.match(self.line)

            if match:
                if self.is_expression():
                    lastBlockLine = self.line
                    lastBlockLineNum = self.lineNum

                    # If the line is indented farther than the first identifier in the block,
                    # it is considered a formatting error.
                    if match.group('indent') and not match.group('indented_expression'):
                        self.error('incorrect indentation')

                    self.get_expression(match)

                    if not self.pairs_balanced(match):
                        return (False, isSingleVar)

                    if self.expression:
                        separatorMatch = self.SEPARATOR_RE.match(self.expression)

                        if separatorMatch is None:
                            # If the assignment does not have a separator, it's an error
                            self.error('missing statement separator', type=self.ERROR_TYPE_ILLEGAL)
                        else:
                            separator = separatorMatch.group('separator')

                            if blockHasSemicolon:
                                # If the block already has a semicolon, we have an accidental global declaration
                                self.error('accidental global variable', type=self.ERROR_TYPE_ILLEGAL)
                            elif (separator == ';'):
                                blockHasSemicolon = True
                    elif match.group('separator'):
                        separator = match.group('separator')

                    isSingleVar = False
                else:
                    # If the line is a control statement of some kind, then it should not be indented this far.
                    self.error('statement should be outdented from preceding var block')
                    return (True, False)

            else:
                # If the line does not match, it is not an assignment or is outdented from the block.
                # In either case, the block is considered closed. If the most recent separator was not ';',
                # the block was not properly terminated.
                if separator != ';':
                    self.error('unterminated var block', lineNum=lastBlockLineNum, line=lastBlockLine, type=self.ERROR_TYPE_ILLEGAL)

                return (True, isSingleVar)

    def check_var_blocks(self):
        lastStatementWasVar = False
        lastVarWasSingle = False
        haveLine = True

        while True:
            if not haveLine:
                haveLine = self.next_statement()

            if not self.is_statement():
                haveLine = False
                continue

            match = self.VAR_BLOCK_START_RE.match(self.line)

            if match is None:
                lastStatementWasVar = False
                haveLine = False
                continue

            # It might be a function definition, in which case we continue
            expression = match.group('expression')

            if expression:
                functionMatch = self.FUNCTION_RE.match(expression)

                if functionMatch:
                    lastStatementWasVar = False
                    haveLine = False
                    continue

            # Now we have the start of a variable block
            if self.verbose:
                print u'%d: VAR BLOCK' % self.lineNum

            varLineNum = self.lineNum
            varLine = self.line

            haveLine, isSingleVar = self.var_block(match)

            if self.verbose:
                print u'%d: END VAR BLOCK:' % self.lineNum,

                if isSingleVar:
                    print u'SINGLE'
                else:
                    print u'MULTIPLE'

            if lastStatementWasVar and self.varDeclarations != self.VAR_DECLARATIONS_NONE:
                if (self.varDeclarations == self.VAR_DECLARATIONS_SINGLE and lastVarWasSingle and isSingleVar) or \
                   (self.varDeclarations == self.VAR_DECLARATIONS_STRICT and (lastVarWasSingle or isSingleVar)):
                    self.error('consecutive var declarations', lineNum=varLineNum, line=varLine)

            lastStatementWasVar = True
            lastVarWasSingle = isSingleVar

    def run_file_checks(self):
        for check in self.fileChecklist:
            self.sourcefile.seek(0)
            self.lineNum = 0

            if self.verbose:
                print u'%s: %s' % (check['title'], self.sourcefile.name)

            check['action']()

    def lint(self, filesToCheck):
        # Recursively walk any directories and eliminate duplicates
        self.filesToCheck = []

        for filename in filesToCheck:
            filename = unicode(filename, 'utf-8')
            fullpath = os.path.join(self.basedir, filename)

            if fullpath not in self.filesToCheck:
                if os.path.isdir(fullpath):
                    for root, dirs, files in os.walk(fullpath):
                        for skipDir in self.DIRS_TO_SKIP:
                            if skipDir in dirs:
                                dirs.remove(skipDir)

                        for filename in files:
                            if not filename.endswith('.j'):
                                continue

                            fullpath = os.path.join(root, filename)

                            if fullpath not in self.filesToCheck:
                                self.filesToCheck.append(fullpath)
                else:
                    self.filesToCheck.append(fullpath)

        for filename in self.filesToCheck:
            try:
                with open(filename) as self.sourcefile:
                    self.filename = relative_path(self.basedir, filename)
                    self.run_file_checks()

            except IOError:
                self.lineNum = 0
                self.line = None
                self.error('file not found', type=self.ERROR_TYPE_ILLEGAL)

            except StopIteration:
                if self.verbose:
                    print u'EOF\n'
                pass

    def lint_text(self, text, filename):
        self.filename = filename
        self.filesToCheck = []

        try:
            self.sourcefile = cStringIO.StringIO(text)
            self.run_file_checks()
        except StopIteration:
            if self.verbose:
                print u'EOF\n'
            pass

    def count_files_checked(self):
        return len(self.filesToCheck)

    def error(self, message, **kwargs):
        info = {
            'filename':  self.filename,
            'message':   message,
            'type':      kwargs.get('type', self.ERROR_TYPE_WARNING)
        }

        line = kwargs.get('line', self.line)
        lineNum = kwargs.get('lineNum', self.lineNum)

        if line and lineNum:
            info['line'] = tabs2spaces(line)
            info['lineNum'] = lineNum

        positions = kwargs.get('positions')

        if positions:
            info['positions'] = positions

        self.errors.append(info)

        if self.filename not in self.errorFiles:
            self.errorFiles.append(self.filename)

    def has_errors(self):
        return len(self.errors) != 0

    def print_errors(self, format='text'):
        if not self.errors:
            return

        if format == 'text':
            self.print_text_errors()
        elif format == 'html':
            self.print_textmate_html_errors()
        elif format == 'tooltip':
            self.print_tooltip_errors()

    def print_text_errors(self):
        sys.stdout.write('%d error' % len(self.errors))

        if len(self.errors) > 1:
            sys.stdout.write('s')

        if len(self.filesToCheck) == 1:
            template = self.TEXT_ERROR_SINGLE_FILE_TEMPLATE
        else:
            sys.stdout.write(' in %d files' % len(self.errorFiles))
            template = self.TEXT_ERROR_MULTI_FILE_TEMPLATE

        sys.stdout.write(':\n\n')

        for error in self.errors:
            if 'lineNum' in error and 'line' in error:
                sys.stdout.write(template.substitute(error).encode('utf-8'))

                if error.get('positions'):
                    markers = ' ' * len(error['line'])

                    for position in error['positions']:
                        markers = markers[:position] + '^' + markers[position + 1:]

                    # Add a space at the beginning of the markers to account for the '+' at the beginning
                    # of the source line.
                    sys.stdout.write(' %s\n' % markers)
            else:
                sys.stdout.write('%s: %s.\n' % (error['filename'], error['message']))

            sys.stdout.write('\n')

    def print_textmate_html_errors(self):
        html = """
<html>
    <head>
        <title>Cappuccino Lint Report</title>
        <style type="text/css">
            body {
                margin: 0px;
                padding: 1px;
            }

            h1 {
                font: bold 12pt "Lucida Grande";
                color: #333;
                background-color: #FF7880;
                margin: 0 0 .5em 0;
                padding: .25em .5em;
            }

            p, a {
                margin: 0px;
                padding: 0px;
            }

            p {
                font: normal 10pt "Lucida Grande";
                color: #000;
            }

            p.error {
                background-color: #E2EAFF;
            }

            p.source {
                font-family: Consolas, 'Bitstream Vera Sans Mono', Monoco, Courier, sans-serif;
                white-space: pre;
                background-color: #fff;
                padding-bottom: 1em;
            }

            a {
                display: block;
                padding: .25em .5em;
                text-decoration: none;
                color: inherit;
                background-color: inherit;
            }

            a:hover {
                background-color: #ddd;
            }

            em {
                font-weight: normal;
                font-style: normal;
                font-variant: normal;
                background-color: #FF7880;
            }
        </style>
    </head>
    <body>
    """

        html += '<h1>Results: %d error' % len(self.errors)

        if len(self.errors) > 1:
            html += 's'

        if len(self.filesToCheck) > 1:
            html += ' in %d files' % len(self.errorFiles)

        html += '</h1>'

        for error in self.errors:
            message = cgi.escape(error['message'])

            if len(self.filesToCheck) > 1:
                filename = cgi.escape(error['filename']) + ':'
            else:
                filename = ''

            html += '<p class="error">'

            if 'line' in error and 'lineNum' in error:
                filepath = cgi.escape(os.path.join(self.basedir, error['filename']))
                lineNum = error['lineNum']
                line = error['line']
                positions = error.get('positions')
                firstPos = -1
                source = ''

                if positions:
                    firstPos = positions[0] + 1
                    lastPos = 0

                    for pos in error.get('positions'):
                        if pos < len(line):
                            charToHighlight = line[pos]
                        else:
                            charToHighlight = ''

                        source += '%s<em>%s</em>' % (cgi.escape(line[lastPos:pos]), cgi.escape(charToHighlight))
                        lastPos = pos + 1

                    if lastPos <= len(line):
                        source += cgi.escape(line[lastPos:])
                else:
                    source = line

                link = '<a href="txmt://open/?url=file://%s&line=%d&column=%d">' % (filepath, lineNum, firstPos)

                if len(self.filesToCheck) > 1:
                    errorMsg = '%s%d: %s' % (filename, lineNum, message)
                else:
                    errorMsg = '%d: %s' % (lineNum, message)

                html += '%(link)s%(errorMsg)s</a></p>\n<p class="source">%(link)s%(source)s</a></p>\n' % {'link': link, 'errorMsg': errorMsg, 'source': source}
            else:
                html += '%s%s</p>\n' % (filename, message)

        html += """
    </body>
</html>
"""
        exit_show_html(html)


if __name__ == '__main__':
    usage = 'usage: %prog [options] [file ... | -]'
    parser = OptionParser(usage=usage, version='1.02')
    parser.add_option('-f', '--format', action='store', type='string', dest='format', default='text', help='the format to use for the report: text (default) or html (HTML in which errors can be clicked on to view in TextMate)')
    parser.add_option('-b', '--basedir', action='store', type='string', dest='basedir', help='the base directory relative to which filenames are resolved, defaults to the current working directory')
    parser.add_option('-d', '--var-declarations', action='store', type='string', dest='var_declarations', default='single', help='set the policy for flagging consecutive var declarations (%s)' % ', '.join(LintChecker.VAR_DECLARATIONS))
    parser.add_option('-v', '--verbose', action='store_true', dest='verbose', default=False, help='show what lint is doing')
    parser.add_option('-q', '--quiet', action='store_true', dest='quiet', default=False, help='do not display errors, only return an exit code')

    (options, args) = parser.parse_args()

    if options.var_declarations not in LintChecker.VAR_DECLARATIONS:
        parser.error('--var-declarations must be one of [' + ', '.join(LintChecker.VAR_DECLARATIONS) + ']')

    if options.verbose and options.quiet:
        parser.error('options -v/--verbose and -q/--quiet are mutually exclusive')

    options.format = options.format.lower()

    if not options.format in LintChecker.ERROR_FORMATS:
        parser.error('format must be one of ' + '/'.join(LintChecker.ERROR_FORMATS))

    if options.format == 'html' and not within_textmate():
        parser.error('html format can only be used within TextMate.')

    if options.basedir:
        basedir = options.basedir

        if basedir[-1] == '/':
            basedir = basedir[:-1]
    else:
        basedir = os.getcwd()

    # We accept a list of filenames (relative to the cwd) either from the command line or from stdin
    filenames = args

    if args and args[0] == '-':
        filenames = [name.rstrip() for name in sys.stdin.readlines()]

    if not filenames:
        print usage.replace('%prog', os.path.basename(sys.argv[0]))
        sys.exit(0)

    checker = LintChecker(basedir=basedir, var_declarations=LintChecker.VAR_DECLARATIONS.index(options.var_declarations), verbose=options.verbose)
    pathsToCheck = []

    for filename in filenames:
        filename = filename.strip('"\'')
        path = os.path.join(basedir, filename)

        if (os.path.isdir(path) and not path.endswith('Frameworks')) or filename.endswith('.j'):
            pathsToCheck.append(relative_path(basedir, filename))

    if len(pathsToCheck) == 0:
        if within_textmate():
            exit_show_tooltip('No Objective-J files found.')

        sys.exit(0)

    checker.lint(pathsToCheck)

    if checker.has_errors():
        if not options.quiet:
            checker.print_errors(options.format)

        sys.exit(1)
    else:
        if within_textmate():
            exit_show_tooltip('Everything looks clean.')

        sys.exit(0)
