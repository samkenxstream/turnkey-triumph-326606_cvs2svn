# (Be in -*- python -*- mode.)
#
# ====================================================================
# Copyright (c) 2000-2006 CollabNet.  All rights reserved.
#
# This software is licensed as described in the file COPYING, which
# you should have received as part of this distribution.  The terms
# are also available at http://subversion.tigris.org/license-1.html.
# If newer versions of this license are posted there, you may use a
# newer version instead, at your option.
#
# This software consists of voluntary contributions made by many
# individuals.  For exact contribution history, see the revision
# history and logs, available at http://cvs2svn.tigris.org/.
# ====================================================================

"""This module contains classes to set cvs2svn run options."""


from __future__ import generators

import sys
import os
import re
import getopt
try:
  my_getopt = getopt.gnu_getopt
except AttributeError:
  my_getopt = getopt.getopt

from cvs2svn_lib.boolean import *
from cvs2svn_lib import config
from cvs2svn_lib.common import warning_prefix
from cvs2svn_lib.common import error_prefix
from cvs2svn_lib.common import FatalError
from cvs2svn_lib.log import Log
from cvs2svn_lib.process import CommandFailedException
from cvs2svn_lib.process import check_command_runs
from cvs2svn_lib.context import Ctx
from cvs2svn_lib.project import Project
from cvs2svn_lib.pass_manager import InvalidPassError
from cvs2svn_lib.symbol_strategy import RuleBasedSymbolStrategy
from cvs2svn_lib.symbol_strategy import ForceBranchRegexpStrategyRule
from cvs2svn_lib.symbol_strategy import ForceTagRegexpStrategyRule
from cvs2svn_lib.symbol_strategy import ExcludeRegexpStrategyRule
from cvs2svn_lib.symbol_strategy import UnambiguousUsageRule
from cvs2svn_lib.symbol_strategy import BranchIfCommitsRule
from cvs2svn_lib.symbol_strategy import HeuristicStrategyRule
from cvs2svn_lib.symbol_strategy import AllBranchRule
from cvs2svn_lib.symbol_strategy import AllTagRule
from cvs2svn_lib import property_setters


usage_message_template = """\
USAGE: %(progname)s [-v] [-s svn-repos-path] [-p pass] cvs-repos-path
  --help, -h           print this usage message and exit with success
  --help-passes        list the available passes and their numbers
  --version            print the version number
  --verbose, -v        verbose
  --quiet, -q          quiet
  -s PATH              path for SVN repos
  -p PASS              execute only specified PASS
  -p [START]:[END]     execute passes START through END, inclusive
                       (PASS, START, and END can be pass names or numbers)
  --existing-svnrepos  load into existing SVN repository
  --dump-only          just produce a dumpfile; don't commit to a repos
  --dumpfile=PATH      name dumpfile to output
  --dry-run            do not create a repository or a dumpfile;
                       just print what would happen.
  --use-cvs            use CVS instead of RCS 'co' to extract data
                       (only use this if having problems with RCS)
  --svnadmin=PATH      path to the svnadmin program
  --trunk-only         convert only trunk commits, not tags nor branches
  --trunk=PATH         path for trunk (default: %(trunk_base)s)
  --branches=PATH      path for branches (default: %(branches_base)s)
  --tags=PATH          path for tags (default: %(tags_base)s)
  --no-prune           don't prune empty directories
  --encoding=ENC       encoding of paths and log messages in CVS repos
                       Multiple of these options may be passed, where they
                       will be treated as an ordered list of encodings to
                       attempt (with "ascii" as a hardcoded last resort)
  --force-branch=REGEXP force symbols matching REGEXP to be branches
  --force-tag=REGEXP   force symbols matching REGEXP to be tags
  --exclude=REGEXP     exclude branches and tags matching REGEXP
  --symbol-default=OPT choose how ambiguous symbols are converted.  OPT is
                       "branch", "tag", or "heuristic", or "strict" (default)
  --symbol-transform=P:S transform symbol names from P to S where P and S
                       use Python regexp and reference syntax respectively
  --username=NAME      username for cvs2svn-synthesized commits
  --fs-type=TYPE       pass --fs-type=TYPE to "svnadmin create"
  --bdb-txn-nosync     pass --bdb-txn-nosync to "svnadmin create"
  --cvs-revnums        record CVS revision numbers as file properties
  --mime-types=FILE    specify an apache-style mime.types file for
                       setting svn:mime-type
  --auto-props=FILE    set file properties from the auto-props section
                       of a file in svn config format
  --auto-props-ignore-case Ignore case when matching auto-props patterns
  --eol-from-mime-type set svn:eol-style from mime type if known
  --no-default-eol     don't set svn:eol-style to 'native' for
                       non-binary files with undetermined mime types
  --keywords-off       don't set svn:keywords on any files (by default,
                       cvs2svn sets svn:keywords on non-binary files to
                       "%(svn_keywords_value)s")
  --tmpdir=PATH        directory to use for tmp data (default to cwd)
  --skip-cleanup       prevent the deletion of intermediate files
  --profile            profile with 'hotshot' (into file cvs2svn.hotshot)
"""

def usage():
  sys.stdout.write(usage_message_template % {
      'progname' : os.path.basename(sys.argv[0]),
      'trunk_base' : Ctx().trunk_base,
      'branches_base' : Ctx().branches_base,
      'tags_base' : Ctx().tags_base,
      'svn_keywords_value' : config.SVN_KEYWORDS_VALUE,
      })


class RunOptions:
  """A place to store meta-options that are used to start the conversion."""

  def __init__(self, pass_manager):
    self.pass_manager = pass_manager
    self.start_pass = 1
    self.end_pass = pass_manager.num_passes
    self.profiling = False


class CommandLineRunOptions(RunOptions):
  def __init__(self, pass_manager):
    """Process the command-line options, storing run options to SELF."""

    RunOptions.__init__(self, pass_manager)

    print_help = False
    quiet = 0
    verbose = 0
    symbol_strategy_default = 'strict'
    mime_types_file = None
    auto_props_file = None
    auto_props_ignore_case = False
    eol_from_mime_type = False
    no_default_eol = False
    keywords_off = False

    # Convenience var, so we don't have to keep instantiating this Borg.
    ctx = Ctx()
    ctx.symbol_strategy = RuleBasedSymbolStrategy()

    try:
      opts, args = my_getopt(sys.argv[1:], 'hvqs:p:', [
          "help", "help-passes", "version",
          "verbose", "quiet",
          "existing-svnrepos", "dump-only", "dumpfile=", "dry-run",
          "use-cvs",
          "svnadmin=",
          "trunk-only",
          "trunk=", "branches=", "tags=",
          "no-prune",
          "encoding=",
          "force-branch=", "force-tag=", "exclude=", "symbol-default=",
          "symbol-transform=",
          "username=",
          "fs-type=", "bdb-txn-nosync",
          "cvs-revnums",
          "mime-types=",
          "auto-props=", "auto-props-ignore-case",
          "eol-from-mime-type", "no-default-eol",
          "keywords-off",
          "tmpdir=",
          "skip-cleanup",
          "profile",
          "create",
          ])
    except getopt.GetoptError, e:
      sys.stderr.write(error_prefix + ': ' + str(e) + '\n\n')
      usage()
      sys.exit(1)

    for opt, value in opts:
      if opt in ['--help', '-h']:
        print_help = True
      elif opt == '--help-passes':
        pass_manager.help_passes()
        sys.exit(0)
      elif opt == '--version':
          print '%s version %s' % (os.path.basename(sys.argv[0]), VERSION)
          sys.exit(0)
      elif opt in ['--verbose', '-v']:
        verbose += 1
      elif opt in ['--quiet', '-q']:
        quiet += 1
      elif opt == '-s':
        ctx.target = value
      elif opt == '-p':
        if value.find(':') >= 0:
          start_pass, end_pass = value.split(':')
          self.start_pass = \
              pass_manager.get_pass_number(start_pass, 1)
          self.end_pass = \
              pass_manager.get_pass_number(end_pass, pass_manager.num_passes)
        else:
          self.end_pass = self.start_pass = \
              pass_manager.get_pass_number(value)

        if not self.start_pass <= self.end_pass:
          raise InvalidPassError(
              'Ending pass must not come before starting pass.')
      elif opt == '--existing-svnrepos':
        ctx.existing_svnrepos = True
      elif opt == '--dump-only':
        ctx.dump_only = True
      elif opt == '--dumpfile':
        ctx.dumpfile = value
      elif opt == '--dry-run':
        ctx.dry_run = True
      elif opt == '--use-cvs':
        ctx.use_cvs = True
      elif opt == '--svnadmin':
        ctx.svnadmin = value
      elif opt == '--trunk-only':
        ctx.trunk_only = True
      elif opt == '--trunk':
        ctx.trunk_base = value
      elif opt == '--branches':
        ctx.branches_base = value
      elif opt == '--tags':
        ctx.tags_base = value
      elif opt == '--no-prune':
        ctx.prune = False
      elif opt == '--encoding':
        ctx.encoding.insert(-1, value)
      elif opt == '--force-branch':
        ctx.symbol_strategy.add_rule(ForceBranchRegexpStrategyRule(value))
      elif opt == '--force-tag':
        ctx.symbol_strategy.add_rule(ForceTagRegexpStrategyRule(value))
      elif opt == '--exclude':
        ctx.symbol_strategy.add_rule(ExcludeRegexpStrategyRule(value))
      elif opt == '--symbol-default':
        if value not in ['branch', 'tag', 'heuristic', 'strict']:
          raise FatalError(
              '%r is not a valid option for --symbol_default.' % (value,))
        symbol_strategy_default = value
      elif opt == '--symbol-transform':
        [pattern, replacement] = value.split(":")
        try:
          pattern = re.compile(pattern)
        except re.error, e:
          raise FatalError("'%s' is not a valid regexp." % (pattern,))
        ctx.symbol_transforms.append((pattern, replacement,))
      elif opt == '--username':
        ctx.username = value
      elif opt == '--fs-type':
        ctx.fs_type = value
      elif opt == '--bdb-txn-nosync':
        ctx.bdb_txn_nosync = True
      elif opt == '--cvs-revnums':
        ctx.svn_property_setters.append(
            property_setters.CVSRevisionNumberSetter())
      elif opt == '--mime-types':
        mime_types_file = value
      elif opt == '--auto-props':
        auto_props_file = value
      elif opt == '--auto-props-ignore-case':
        auto_props_ignore_case = True
      elif opt == '--eol-from-mime-type':
        eol_from_mime_type = True
      elif opt == '--no-default-eol':
        no_default_eol = True
      elif opt == '--keywords-off':
        keywords_off = True
      elif opt == '--tmpdir':
        ctx.tmpdir = value
      elif opt == '--skip-cleanup':
        ctx.skip_cleanup = True
      elif opt == '--profile':
        self.profiling = True
      elif opt == '--create':
        sys.stderr.write(warning_prefix +
            ': The behaviour produced by the --create option is now the '
            'default,\nand passing the option is deprecated.\n')

    if print_help:
      usage()
      sys.exit(0)

    # Consistency check for options and arguments.
    if len(args) == 0:
      usage()
      sys.exit(1)

    if len(args) > 1:
      sys.stderr.write(error_prefix +
                       ": must pass only one CVS repository.\n")
      usage()
      sys.exit(1)

    cvsroot = args[0]

    if (not ctx.target) and (not ctx.dump_only) and (not ctx.dry_run):
      raise FatalError("must pass one of '-s' or '--dump-only'.")

    def not_both(opt1val, opt1name, opt2val, opt2name):
      if opt1val and opt2val:
        raise FatalError("cannot pass both '%s' and '%s'."
                         % (opt1name, opt2name,))

    not_both(ctx.target, '-s',
             ctx.dump_only, '--dump-only')

    not_both(ctx.dump_only, '--dump-only',
             ctx.existing_svnrepos, '--existing-svnrepos')

    not_both(ctx.bdb_txn_nosync, '--bdb-txn-nosync',
             ctx.existing_svnrepos, '--existing-svnrepos')

    not_both(ctx.dump_only, '--dump-only',
             ctx.bdb_txn_nosync, '--bdb-txn-nosync')

    not_both(quiet, '-q',
             verbose, '-v')

    not_both(ctx.fs_type, '--fs-type',
             ctx.existing_svnrepos, '--existing-svnrepos')

    if ctx.fs_type and ctx.fs_type != 'bdb' and ctx.bdb_txn_nosync:
      raise FatalError("cannot pass --bdb-txn-nosync with --fs-type=%s."
                       % ctx.fs_type)

    if verbose:
      Log().log_level = Log.VERBOSE
    elif quiet == 1:
      Log().log_level = Log.QUIET
    elif quiet >= 2:
      Log().log_level = Log.WARN
    else:
      Log().log_level = Log.NORMAL

    # Create the default project (using ctx.trunk, ctx.branches, and
    # ctx.tags):
    ctx.add_project(Project(
        len(ctx.projects),
        cvsroot, ctx.trunk_base, ctx.branches_base, ctx.tags_base))

    if ctx.existing_svnrepos and not os.path.isdir(ctx.target):
      raise FatalError("the svn-repos-path '%s' is not an "
                       "existing directory." % ctx.target)

    if not ctx.dump_only and not ctx.existing_svnrepos \
       and (not ctx.dry_run) and os.path.exists(ctx.target):
      raise FatalError("the svn-repos-path '%s' exists.\n"
                       "Remove it, or pass '--existing-svnrepos'."
                       % ctx.target)

    if ctx.target and not ctx.dry_run:
      # Verify that svnadmin can be executed.  The 'help' subcommand
      # should be harmless.
      try:
        check_command_runs([ctx.svnadmin, 'help'], 'svnadmin')
      except CommandFailedException, e:
        raise FatalError(
            '%s\n'
            'svnadmin could not be executed.  Please ensure that it is\n'
            'installed and/or use the --svnadmin option.' % (e,))

    ctx.symbol_strategy.add_rule(UnambiguousUsageRule())
    if symbol_strategy_default == 'strict':
      pass
    elif symbol_strategy_default == 'branch':
      ctx.symbol_strategy.add_rule(AllBranchRule())
    elif symbol_strategy_default == 'tag':
      ctx.symbol_strategy.add_rule(AllTagRule())
    elif symbol_strategy_default == 'heuristic':
      ctx.symbol_strategy.add_rule(BranchIfCommitsRule())
      ctx.symbol_strategy.add_rule(HeuristicStrategyRule())
    else:
      assert False

    ctx.svn_property_setters.append(
        property_setters.ExecutablePropertySetter())

    ctx.svn_property_setters.append(
        property_setters.BinaryFileEOLStyleSetter())

    if mime_types_file:
      ctx.svn_property_setters.append(
          property_setters.MimeMapper(mime_types_file))

    if auto_props_file:
      ctx.svn_property_setters.append(
          property_setters.AutoPropsPropertySetter(
              auto_props_file, auto_props_ignore_case))

    ctx.svn_property_setters.append(
        property_setters.BinaryFileDefaultMimeTypeSetter())

    if eol_from_mime_type:
      ctx.svn_property_setters.append(
          property_setters.EOLStyleFromMimeTypeSetter())

    if no_default_eol:
      ctx.svn_property_setters.append(
          property_setters.DefaultEOLStyleSetter(None))
    else:
      ctx.svn_property_setters.append(
          property_setters.DefaultEOLStyleSetter('native'))

    if not keywords_off:
      ctx.svn_property_setters.append(
          property_setters.KeywordsPropertySetter(config.SVN_KEYWORDS_VALUE))


class OptionsFileRunOptions(RunOptions):
  def __init__(self, pass_manager, options_filename):
    """Read options from the file named OPTIONS_FILENAME.

    Store the run options to SELF."""

    RunOptions.__init__(self, pass_manager)

    g = {}
    l = {
      'ctx' : Ctx(),
      'run_options' : self,
      }
    execfile(options_filename, g, l)

