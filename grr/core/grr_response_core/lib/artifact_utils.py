#!/usr/bin/env python
"""Library for processing of artifacts.

This file contains non-GRR specific pieces of artifact processing and is
intended to end up as an independent library.
"""

from __future__ import absolute_import
from __future__ import division

from __future__ import unicode_literals

import re

from future.utils import string_types
from typing import Iterable
from typing import Text

from grr_response_core.lib import interpolation
from grr_response_core.lib import objectfilter
from grr_response_core.lib.rdfvalues import structs as rdf_structs


class Error(Exception):
  """Base exception."""


class ConditionError(Error):
  """An invalid artifact condition was specified."""


class ArtifactProcessingError(Error):
  """Unable to process artifact."""


class KbInterpolationMissingAttributesError(Error):
  """An exception class for missing knowledgebase attributes."""

  def __init__(self, attrs):
    message = "Some attributes could not be located in the knowledgebase: {}"
    message = message.format(", ".join(attrs))
    super(KbInterpolationMissingAttributesError, self).__init__(message)

    self.attrs = list(attrs)


class KbInterpolationUnknownAttributesError(Error):
  """An exception class for non-existing knowledgebase attributes."""

  def __init__(self, attrs):
    message = "Some attributes are not part of the knowledgebase: {}"
    message = message.format(", ".join(attrs))
    super(KbInterpolationUnknownAttributesError, self).__init__(message)

    self.attrs = list(attrs)


class KnowledgeBaseUninitializedError(Error):
  """Attempt to process artifact without a valid Knowledge Base."""


class KnowledgeBaseAttributesMissingError(Error):
  """Knowledge Base is missing key attributes."""


INTERPOLATED_REGEX = re.compile(r"%%([^%]+?)%%")


def InterpolateListKbAttributes(input_list, knowledge_base):
  interpolated_list = []
  for element in input_list:
    interpolated_list.extend(InterpolateKbAttributes(element, knowledge_base))
  return interpolated_list


def InterpolateKbAttributes(pattern, knowledge_base):
  """Interpolate all knowledgebase attributes in pattern.

  Args:
    pattern: A string with potential interpolation markers. For example:
      "/home/%%users.username%%/Downloads/"
    knowledge_base: The knowledge_base to interpolate parameters from.

  Raises:
    KbInterpolationMissingAttributesError: If any of the required pattern
      parameters is not present in the knowledgebase.
    KbInterpolationUnknownAttributesError: If any of the specified pattern
      parameters is not a valid knowledgebase attribute.

  Returns:
    An iterator over all unique strings generated by expanding the pattern.
  """
  # TODO(hanuszczak): Importing `rdf_client` module (where knowledgebase RDF
  # class is defined) causes a cyclic dependency. As a workaround, we get the
  # class object here but it is obviously a terrible solution and modules should
  # be refactored instead.
  kb_cls = knowledge_base.__class__

  # TODO(hanuszczak): Control flow feels a bit awkward here because of error
  # handling that tries not to break any functionality. With the new utilities
  # it should be possible to improve the code, changing the behaviour to a more
  # sane one.
  interpolator = interpolation.Interpolator(pattern)

  missing_attr_names = set()
  unknown_attr_names = set()

  for var_id in interpolator.Vars():
    var_name = str(var_id).lower()

    if var_name not in kb_cls.type_infos:
      unknown_attr_names.add(var_name)

  for scope_id in interpolator.Scopes():
    scope_name = str(scope_id).lower()

    if not (scope_name in kb_cls.type_infos and
            isinstance(kb_cls.type_infos[scope_name], rdf_structs.ProtoList)):
      unknown_attr_names.add(scope_name)
      continue

    scope_type = kb_cls.type_infos[scope_name].delegate.type

    for var_id in interpolator.ScopeVars(scope_id):
      var_name = str(var_id).lower()

      if var_name not in scope_type.type_infos:
        unknown_attr_names.add("{}.{}".format(scope_name, var_name))
        continue

  if unknown_attr_names:
    raise KbInterpolationUnknownAttributesError(unknown_attr_names)

  for vid in interpolator.Vars():
    attr_name = str(vid).lower()

    value = getattr(knowledge_base, attr_name)
    if not value:
      missing_attr_names.add(attr_name)
      continue

    interpolator.BindVar(attr_name, value)

  for scope_id in interpolator.Scopes():
    scope_name = str(scope_id).lower()

    kb_structs = getattr(knowledge_base, scope_name)
    if not kb_structs:
      missing_attr_names.add(scope_name)
      continue

    scope_bound = False
    scope_missing_attr_names = set()

    for kb_struct in kb_structs:
      bindings = {}

      var_ids = interpolator.ScopeVars(scope_id)
      for var_id in var_ids:
        attr_name = str(var_id).lower()

        value = getattr(kb_struct, attr_name)
        if not value:
          scope_missing_attr_names.add("{}.{}".format(scope_name, attr_name))
          continue

        bindings[var_id] = value

      if len(bindings) == len(var_ids):
        interpolator.BindScope(scope_id, bindings)
        scope_bound = True

    if not scope_bound:
      missing_attr_names.update(scope_missing_attr_names)

  if missing_attr_names:
    raise KbInterpolationMissingAttributesError(missing_attr_names)

  return interpolator.Interpolate()


def GetWindowsEnvironmentVariablesMap(knowledge_base):
  """Return a dictionary of environment variables and their values.

  Implementation maps variables mentioned in
  https://en.wikipedia.org/wiki/Environment_variable#Windows to known
  KB definitions.

  Args:
    knowledge_base: A knowledgebase object.

  Returns:
    A dictionary built from a given knowledgebase object where keys are
    variables names and values are their values.
  """

  environ_vars = {}

  if knowledge_base.environ_path:
    environ_vars["path"] = knowledge_base.environ_path

  if knowledge_base.environ_temp:
    environ_vars["temp"] = knowledge_base.environ_temp

  if knowledge_base.environ_systemroot:
    environ_vars["systemroot"] = knowledge_base.environ_systemroot

  if knowledge_base.environ_windir:
    environ_vars["windir"] = knowledge_base.environ_windir

  if knowledge_base.environ_programfiles:
    environ_vars["programfiles"] = knowledge_base.environ_programfiles
    environ_vars["programw6432"] = knowledge_base.environ_programfiles

  if knowledge_base.environ_programfilesx86:
    environ_vars["programfiles(x86)"] = knowledge_base.environ_programfilesx86

  if knowledge_base.environ_systemdrive:
    environ_vars["systemdrive"] = knowledge_base.environ_systemdrive

  if knowledge_base.environ_allusersprofile:
    environ_vars["allusersprofile"] = knowledge_base.environ_allusersprofile
    environ_vars["programdata"] = knowledge_base.environ_allusersprofile

  if knowledge_base.environ_allusersappdata:
    environ_vars["allusersappdata"] = knowledge_base.environ_allusersappdata

  for user in knowledge_base.users:
    if user.appdata:
      environ_vars.setdefault("appdata", []).append(user.appdata)

    if user.localappdata:
      environ_vars.setdefault("localappdata", []).append(user.localappdata)

    if user.userdomain:
      environ_vars.setdefault("userdomain", []).append(user.userdomain)

    if user.userprofile:
      environ_vars.setdefault("userprofile", []).append(user.userprofile)

  return environ_vars


def ExpandWindowsEnvironmentVariables(data_string, knowledge_base):
  r"""Take a string and expand any windows environment variables.

  Args:
    data_string: A string, e.g. "%SystemRoot%\\LogFiles"
    knowledge_base: A knowledgebase object.

  Returns:
    A string with available environment variables expanded. If we can't expand
    we just return the string with the original variables.
  """
  win_environ_regex = re.compile(r"%([^%]+?)%")
  components = []
  offset = 0
  for match in win_environ_regex.finditer(data_string):
    components.append(data_string[offset:match.start()])

    # KB environment variables are prefixed with environ_.
    kb_value = getattr(knowledge_base, "environ_%s" % match.group(1).lower(),
                       None)
    if isinstance(kb_value, string_types) and kb_value:
      components.append(kb_value)
    else:
      # Failed to expand, leave the variable as it was.
      components.append("%%%s%%" % match.group(1))
    offset = match.end()
  components.append(data_string[offset:])  # Append the final chunk.
  return "".join(components)


def CheckCondition(condition, check_object):
  """Check if a condition matches an object.

  Args:
    condition: A string condition e.g. "os == 'Windows'"
    check_object: Object to validate, e.g. an rdf_client.KnowledgeBase()

  Returns:
    True or False depending on whether the condition matches.

  Raises:
    ConditionError: If condition is bad.
  """
  try:
    of = objectfilter.Parser(condition).Parse()
    compiled_filter = of.Compile(objectfilter.BaseFilterImplementation)
    return compiled_filter.Matches(check_object)
  except objectfilter.Error as e:
    raise ConditionError(e)


def ExpandWindowsUserEnvironmentVariables(data_string,
                                          knowledge_base,
                                          sid=None,
                                          username=None):
  r"""Take a string and expand windows user environment variables based.

  Args:
    data_string: A string, e.g. "%TEMP%\\LogFiles"
    knowledge_base: A knowledgebase object.
    sid: A Windows SID for a user to expand for.
    username: A Windows user name to expand for.

  Returns:
    A string with available environment variables expanded.
  """
  win_environ_regex = re.compile(r"%([^%]+?)%")
  components = []
  offset = 0
  for match in win_environ_regex.finditer(data_string):
    components.append(data_string[offset:match.start()])
    kb_user = knowledge_base.GetUser(sid=sid, username=username)
    kb_value = None
    if kb_user:
      kb_value = getattr(kb_user, match.group(1).lower(), None)
    if isinstance(kb_value, string_types) and kb_value:
      components.append(kb_value)
    else:
      components.append("%%%s%%" % match.group(1))
    offset = match.end()

  components.append(data_string[offset:])  # Append the final chunk.
  return "".join(components)
