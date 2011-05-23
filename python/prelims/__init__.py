"""Preliminary initialisation stuff."""

#   Copyright (C) 2011 Stephen Fairchild (s-fairchild@users.sourceforge.net)
#   This program is free software: you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation, either version 2 of the License, or
#   (at your option) any later version.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with this program in the file entitled COPYING.
#   If not, see <http://www.gnu.org/licenses/>.


__all__ = ["ArgumentParser", "ProfileSelector"]


import os
import sys
import argparse
import shutil
import tempfile
from functools import partial

import dbus
import glib

from idjc import FGlobs
from idjc import PGlobs
from ..utils import Singleton



class ArgumentParserError(Exception):
   pass



class ArgumentParser(argparse.ArgumentParser):
   def error(self, text):
      raise ArgumentParserError(text)

      
   def exit_with_message(self, text):
      """This is just error on the superclass."""
      
      super(ArgumentParser, self).error(text)



class ArgumentParserImplementation(object):
   """To parse the command line arguments, if any."""

   __metaclass__ = Singleton


   def __init__(self, args=None, description=None, epilog=None):
      if args is None:
         args = sys.argv[1:]

      self._args = list(args)

      if description is None:
         description = PGlobs.app_longform

      ap = self._ap = ArgumentParser(description=description, epilog=epilog)
      ap.add_argument("-v", "--version", action='version', version=
                     FGlobs.package_name + " " + FGlobs.package_version)
      sp = self._sp = ap.add_subparsers(
                     help="sub-option -h for more info")
      sp_run = sp.add_parser("run", help="the default command",
         description=description + " -- sub-command: run", epilog=epilog)
      sp_mp = sp.add_parser("generateprofile", help="make a new profile",
         description=description + " -- sub-command: generateprofile", epilog=epilog)

      sp_run.add_argument("-d", "--dialog", dest="dialog", nargs=1, 
            choices=("true", "false"), 
            help="""force the appearance or non-appearance of the
            profile chooser dialog -- when used with the -p option
            the chosen profile is preselected""")
      sp_run.add_argument("-p", "--profile", dest="profile", nargs=1, metavar="profile_choice", 
            help="""the profile to use -- overrides the user interface
            preferences "show profile dialog" option""")
      sp_run.add_argument("-j", "--jackserver", dest="jackserver", nargs=1,
            metavar="server_name", help="the named jack sound-server to connect with")
      group = sp_run.add_argument_group("user interface settings")
      group.add_argument("-m", "--mics", dest="mics", nargs="+", metavar="m",
            help="microphones open at startup")
      group.add_argument("-a", "--aux", dest="aux", nargs="+", metavar="a",
            help="aux ports open at startup")
      group.add_argument("-V", "--voip", dest="voip", nargs=1, choices=
            ("off", "private", "public"),
            help="the voip mode at startup")
      group.add_argument("-P", "--players", dest="players", nargs="+", metavar="p",
            help="the players to start among values {1,2}")
      group.add_argument("-s", "--servers", dest="servers", nargs="+", metavar="s",
            help="attempt connection with the specified servers")
      group.add_argument("-r", "--recorders", dest="recorders", nargs="+", metavar="r",
            help="the recorders to start")
      group.add_argument("-c", "--crossfader", dest="crossfader", choices=("1", "2"), 
            help="position the crossfader for the specified player")
      sp_mp.add_argument("newprofile", metavar="profile_name", help="new profile name")
      sp_mp.add_argument("-t", "--template", dest="template", metavar="template_profile",
            help="an existing profile to use as a template")
      sp_mp.add_argument("-i", "--icon", dest="icon", metavar="icon_pathname",
            help="pathname to an icon -- defaults to idjc logo")
      sp_mp.add_argument("-d", "--description", dest="description", metavar="description_text",
            help="description of the profile")


   def parse_args(self):
      try:
         return self._ap.parse_args(self._args)
      except ArgumentParserError as e:
         try:
            for cmd in self._sp.choices.iterkeys():
               if cmd in self._args:
                  raise
            return self._ap.parse_args(self._args + ["run"])
         except ArgumentParserError:
            self._ap.exit_with_message(str(e))


   def error(self, text):
      self._ap.exit_with_message(text)
      
      
   def exit(self, status=0, message=None):
      self._ap.exit(status, message)
     


class ProfileSelector(object):
   """The profile gives each application instance a unique identity.
   
   This identity extends to the config file directory if present, 
   to the JACK application ID, to the DBus bus name.
   """
   
   __metaclass__ = Singleton
   

   class ProfileError(Exception):
      pass


   def __init__(self, enable_profile_dialog=True, title="Profile Selector"):
      ap = ArgumentParserImplementation()
      args = ap.parse_args()
      choose_profile = partial(self._choose_profile, ap, args,
                                       enable_profile_dialog, title)

      if PGlobs.config_dir is not None:
         try:
            if not os.path.isdir(os.path.join(PGlobs.config_dir, "default")):
               self.generate_profile("default", description="The default profile")

            if "newprofile" in args:
               self.generate_profile(**vars(args))
               ap.exit(0)
         except ProfileError as e:
            ap.error("failed to create profile: " + str(e))

         choose_profile()


   @property
   def profile(self):
      return self._profile


   def _choose_profile(self, ap, args, profile_dialog, title):
      self._profile = "default"
      show_pd = not os.path.exists(os.path.join(PGlobs.config_dir,
                           PGlobs.profile_dialog_refusal_file))
      if args.profile is not None:
         self._profile = args.profile[0]
         show_pd = False
         if not self._profile_name_valid(self._profile):
            ap.error("specified profile name is not valid")

      if args.dialog is not None:
         show_pd = args.dialog[0] == "true"
      
      if show_pd and profile_dialog:
         self._profile = self._profile_choice_by_dialog(title, self._profile)

      return self._profile
      
      
   def _generate_profile(self, newprofile, template=None, **kwds):
      if PGlobs.config_dir is not None:
         if not self._profile_name_valid(newprofile):
            raise self.ProfileError("new profile is not valid")
            
         try:
            tmp = tempfile.mkdtemp()
         except EnvironmentError:
            raise self.ProfileError("temporary directory creation failed")
            
         try:
            if template is not None:
               if not self._profile_name_valid(template):
                  raise self.ProfileError("specified template not valid (%s)" % template)
               
               tdir = os.path.join(PGlobs.config_dir, template)
               if os.path.isdir(tdir):
                  for x in ("icon", "description", "config"):
                     try:
                        shutil.copyfile(os.path.join(tdir, x),
                                        os.path.join(tmp, x))
                     except EnvironmentError:
                        pass
               else:
                  raise self.ProfileError("template profile (%s) does not exist" % template)
                  
            for fname in ("icon", "description"):
               if kwds[fname]:
                  try:
                     with open(os.path.join(tmp, fname), "w") as f:
                        f.write(kwds[fname])
                  except EnvironmentError:
                     raise self.ProfileError("could not write " + fname)
            
            try:
               dest = os.path.join(PGlobs.config_dir, newprofile)
               shutil.copytree(tmp, dest)
            except EnvironmentError as e:
               if e.errno == 17 and os.path.isdir(dest):
                  msg = "profile directory exists"
               else:
                  msg = "could not create profile directory: path exists"
               raise self.ProfileError(msg)
         finally:
            # Failure to clean up is not a critical error.
            try:
               shutil.rmtree(tmp)
            except EnvironmentError:
               pass


   @staticmethod
   def _profile_name_valid(p):
      try:
         dbus.validate_bus_name("com." + p)
         dbus.validate_object_path("/" + p)
      except (TypeError, ValueError):
         return False
      return True


   def _profile_choice_by_dialog(self, title, highlight):
      import gtk
      
      class Dialog(gtk.Dialog):
         pass
         
      return highlight
