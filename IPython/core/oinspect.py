# -*- coding: utf-8 -*-
"""Tools for inspecting Python objects.

Uses syntax highlighting for presenting the various information elements.

Similar in spirit to the inspect module, but all calls take a name argument to
reference the name under which an object is being read.
"""

#*****************************************************************************
#       Copyright (C) 2001-2004 Fernando Perez <fperez@colorado.edu>
#
#  Distributed under the terms of the BSD License.  The full license is in
#  the file COPYING, distributed as part of this software.
#*****************************************************************************

__all__ = ['Inspector','InspectColors']

# stdlib modules
import __builtin__
import StringIO
import inspect
import linecache
import os
import sys
import types
from collections import namedtuple
from itertools import izip_longest

# IPython's own
from IPython.core import page
from IPython.external.Itpl import itpl
from IPython.utils import PyColorize
import IPython.utils.io
from IPython.utils.text import indent
from IPython.utils.wildcard import list_namespace
from IPython.utils.coloransi import *

#****************************************************************************
# Builtin color schemes

Colors = TermColors  # just a shorthand

# Build a few color schemes
NoColor = ColorScheme(
    'NoColor',{
    'header' : Colors.NoColor,
    'normal' : Colors.NoColor  # color off (usu. Colors.Normal)
    }  )

LinuxColors = ColorScheme(
    'Linux',{
    'header' : Colors.LightRed,
    'normal' : Colors.Normal  # color off (usu. Colors.Normal)
    } )

LightBGColors = ColorScheme(
    'LightBG',{
    'header' : Colors.Red,
    'normal' : Colors.Normal  # color off (usu. Colors.Normal)
    }  )

# Build table of color schemes (needed by the parser)
InspectColors = ColorSchemeTable([NoColor,LinuxColors,LightBGColors],
                                 'Linux')

#****************************************************************************
# Auxiliary functions and objects

# See the messaging spec for the definition of all these fields.  This list
# effectively defines the order of display
info_fields = ['type_name', 'base_class', 'string_form', 'namespace',
               'length', 'file', 'definition', 'docstring', 'source',
               'init_definition', 'class_docstring', 'init_docstring',
               'call_def', 'call_docstring',
               # These won't be printed but will be used to determine how to
               # format the object
               'ismagic', 'isalias', 'argspec', 'found', 'name',
               ]


def object_info(**kw):
    """Make an object info dict with all fields present."""
    infodict = dict(izip_longest(info_fields, [None]))
    infodict.update(kw)
    return infodict


def getdoc(obj):
    """Stable wrapper around inspect.getdoc.

    This can't crash because of attribute problems.

    It also attempts to call a getdoc() method on the given object.  This
    allows objects which provide their docstrings via non-standard mechanisms
    (like Pyro proxies) to still be inspected by ipython's ? system."""

    ds = None  # default return value
    try:
        ds = inspect.getdoc(obj)
    except:
        # Harden against an inspect failure, which can occur with
        # SWIG-wrapped extensions.
        pass
    # Allow objects to offer customized documentation via a getdoc method:
    try:
        ds2 = obj.getdoc()
    except:
        pass
    else:
        # if we get extra info, we add it to the normal docstring.
        if ds is None:
            ds = ds2
        else:
            ds = '%s\n%s' % (ds,ds2)
    return ds


def getsource(obj,is_binary=False):
    """Wrapper around inspect.getsource.

    This can be modified by other projects to provide customized source
    extraction.

    Inputs:

    - obj: an object whose source code we will attempt to extract.

    Optional inputs:

    - is_binary: whether the object is known to come from a binary source.
    This implementation will skip returning any output for binary objects, but
    custom extractors may know how to meaningfully process them."""
    
    if is_binary:
        return None
    else:
        try:
            src = inspect.getsource(obj)
        except TypeError:
            if hasattr(obj,'__class__'):
                src = inspect.getsource(obj.__class__)
        return src

def getargspec(obj):
    """Get the names and default values of a function's arguments.

    A tuple of four things is returned: (args, varargs, varkw, defaults).
    'args' is a list of the argument names (it may contain nested lists).
    'varargs' and 'varkw' are the names of the * and ** arguments or None.
    'defaults' is an n-tuple of the default values of the last n arguments.

    Modified version of inspect.getargspec from the Python Standard
    Library."""

    if inspect.isfunction(obj):
        func_obj = obj
    elif inspect.ismethod(obj):
        func_obj = obj.im_func
    elif hasattr(obj, '__call__'):
        func_obj = obj.__call__
    else:
        raise TypeError('arg is not a Python function')
    args, varargs, varkw = inspect.getargs(func_obj.func_code)
    return args, varargs, varkw, func_obj.func_defaults


def format_argspec(argspec):
    """Format argspect, convenience wrapper around inspect's.

    This takes a dict instead of ordered arguments and calls
    inspect.format_argspec with the arguments in the necessary order.
    """
    return inspect.formatargspec(argspec['args'], argspec['varargs'],
                                 argspec['varkw'], argspec['defaults'])


def call_tip(oinfo, format_call=True):
    """Extract call tip data from an oinfo dict.

    Parameters
    ----------
    oinfo : dict

    format_call : bool, optional
      If True, the call line is formatted and returned as a string.  If not, a
      tuple of (name, argspec) is returned.

    Returns
    -------
    call_info : None, str or (str, dict) tuple.
      When format_call is True, the whole call information is formattted as a
      single string.  Otherwise, the object's name and its argspec dict are
      returned.  If no call information is available, None is returned.
      
    docstring : str or None
      The most relevant docstring for calling purposes is returned, if
      available.  The priority is: call docstring for callable instances, then
      constructor docstring for classes, then main object's docstring otherwise
      (regular functions).
    """
    # Get call definition
    argspec = oinfo['argspec']
    if argspec is None:
        call_line = None
    else:
        # Callable objects will have 'self' as their first argument, prune
        # it out if it's there for clarity (since users do *not* pass an
        # extra first argument explicitly).
        try:
            has_self = argspec['args'][0] == 'self'
        except (KeyError, IndexError):
            pass
        else:
            if has_self:
                argspec['args'] = argspec['args'][1:]
                
        call_line = oinfo['name']+format_argspec(argspec)

    # Now get docstring.
    # The priority is: call docstring, constructor docstring, main one.
    doc = oinfo['call_docstring']
    if doc is None:
        doc = oinfo['init_docstring']
    if doc is None:
        doc = oinfo['docstring']

    return call_line, doc

#****************************************************************************
# Class definitions

class myStringIO(StringIO.StringIO):
    """Adds a writeln method to normal StringIO."""
    def writeln(self,*arg,**kw):
        """Does a write() and then a write('\n')"""
        self.write(*arg,**kw)
        self.write('\n')


class Inspector:
    def __init__(self, color_table=InspectColors,
                 code_color_table=PyColorize.ANSICodeColors,
                 scheme='NoColor',
                 str_detail_level=0):
        self.color_table = color_table
        self.parser = PyColorize.Parser(code_color_table,out='str')
        self.format = self.parser.format
        self.str_detail_level = str_detail_level
        self.set_active_scheme(scheme)

    def _getdef(self,obj,oname=''):
        """Return the definition header for any callable object.

        If any exception is generated, None is returned instead and the
        exception is suppressed."""
        
        try:
            # We need a plain string here, NOT unicode!
            hdef = oname + inspect.formatargspec(*getargspec(obj))
            return hdef.encode('ascii')
        except:
            return None
 
    def __head(self,h):
        """Return a header string with proper colors."""
        return '%s%s%s' % (self.color_table.active_colors.header,h,
                           self.color_table.active_colors.normal)

    def set_active_scheme(self,scheme):
        self.color_table.set_active_scheme(scheme)
        self.parser.color_table.set_active_scheme(scheme)
    
    def noinfo(self,msg,oname):
        """Generic message when no information is found."""
        print 'No %s found' % msg,
        if oname:
            print 'for %s' % oname
        else:
            print
            
    def pdef(self,obj,oname=''):
        """Print the definition header for any callable object.

        If the object is a class, print the constructor information."""

        if not callable(obj):
            print 'Object is not callable.'
            return

        header = ''

        if inspect.isclass(obj):
            header = self.__head('Class constructor information:\n')
            obj = obj.__init__
        elif type(obj) is types.InstanceType:
            obj = obj.__call__

        output = self._getdef(obj,oname)
        if output is None:
            self.noinfo('definition header',oname)
        else:
            print >>IPython.utils.io.Term.cout, header,self.format(output),

    def pdoc(self,obj,oname='',formatter = None):
        """Print the docstring for any object.

        Optional:
        -formatter: a function to run the docstring through for specially
        formatted docstrings."""

        head = self.__head  # so that itpl can find it even if private
        ds = getdoc(obj)
        if formatter:
            ds = formatter(ds)
        if inspect.isclass(obj):
            init_ds = getdoc(obj.__init__)
            output = itpl('$head("Class Docstring:")\n'
                          '$indent(ds)\n'
                          '$head("Constructor Docstring"):\n'
                          '$indent(init_ds)')
        elif (type(obj) is types.InstanceType or isinstance(obj,object)) \
                 and hasattr(obj,'__call__'):
            call_ds = getdoc(obj.__call__)
            if call_ds:
                output = itpl('$head("Class Docstring:")\n$indent(ds)\n'
                              '$head("Calling Docstring:")\n$indent(call_ds)')
            else:
                output = ds
        else:
            output = ds
        if output is None:
            self.noinfo('documentation',oname)
            return
        page.page(output)
    
    def psource(self,obj,oname=''):
        """Print the source code for an object."""

        # Flush the source cache because inspect can return out-of-date source
        linecache.checkcache()
        try:
            src = getsource(obj) 
        except:
            self.noinfo('source',oname)
        else:
            page.page(self.format(src))

    def pfile(self,obj,oname=''):
        """Show the whole file where an object was defined."""

        try:
            try:
                lineno = inspect.getsourcelines(obj)[1]
            except TypeError:
                # For instances, try the class object like getsource() does
                if hasattr(obj,'__class__'):
                    lineno = inspect.getsourcelines(obj.__class__)[1]
                    # Adjust the inspected object so getabsfile() below works
                    obj = obj.__class__
        except:
            self.noinfo('file',oname)
            return

        # We only reach this point if object was successfully queried
        
        # run contents of file through pager starting at line
        # where the object is defined
        ofile = inspect.getabsfile(obj)

        if (ofile.endswith('.so') or ofile.endswith('.dll')):
            print 'File %r is binary, not printing.' % ofile
        elif not os.path.isfile(ofile):
            print 'File %r does not exist, not printing.' % ofile
        else:
            # Print only text files, not extension binaries.  Note that
            # getsourcelines returns lineno with 1-offset and page() uses
            # 0-offset, so we must adjust.
            page.page(self.format(open(ofile).read()),lineno-1)

    def pinfo(self,obj,oname='',formatter=None,info=None,detail_level=0):
        """Show detailed information about an object.

        Optional arguments:
        
        - oname: name of the variable pointing to the object.

        - formatter: special formatter for docstrings (see pdoc)

        - info: a structure with some information fields which may have been
        precomputed already.

        - detail_level: if set to 1, more information is given.
        """

        obj_type = type(obj)

        header = self.__head
        if info is None:
            ismagic = 0
            isalias = 0
            ospace = ''
        else:
            ismagic = info.ismagic
            isalias = info.isalias
            ospace = info.namespace
        # Get docstring, special-casing aliases:
        if isalias:
            if not callable(obj):
                try:
                    ds = "Alias to the system command:\n  %s" % obj[1]
                except:
                    ds = "Alias: " + str(obj)
            else:
                ds = "Alias to " + str(obj)
                if obj.__doc__:
                    ds += "\nDocstring:\n" + obj.__doc__
        else:
            ds = getdoc(obj)
            if ds is None:
                ds = '<no docstring>'
        if formatter is not None:
            ds = formatter(ds)

        # store output in a list which gets joined with \n at the end.
        out = myStringIO()
        
        string_max = 200 # max size of strings to show (snipped if longer)
        shalf = int((string_max -5)/2)

        if ismagic:
            obj_type_name = 'Magic function'
        elif isalias:
            obj_type_name = 'System alias'
        else:
            obj_type_name = obj_type.__name__
        out.writeln(header('Type:\t\t')+obj_type_name)

        try:
            bclass = obj.__class__
            out.writeln(header('Base Class:\t')+str(bclass))
        except: pass

        # String form, but snip if too long in ? form (full in ??)
        if detail_level >= self.str_detail_level:
            try:
                ostr = str(obj)
                str_head = 'String Form:'
                if not detail_level and len(ostr)>string_max:
                    ostr = ostr[:shalf] + ' <...> ' + ostr[-shalf:]
                    ostr = ("\n" + " " * len(str_head.expandtabs())).\
                            join(q.strip() for q in ostr.split("\n"))
                if ostr.find('\n') > -1:
                    # Print multi-line strings starting at the next line.
                    str_sep = '\n'
                else:
                    str_sep = '\t'
                out.writeln("%s%s%s" % (header(str_head),str_sep,ostr))
            except:
                pass

        if ospace:
            out.writeln(header('Namespace:\t')+ospace)

        # Length (for strings and lists)
        try:
            length = str(len(obj))
            out.writeln(header('Length:\t\t')+length)
        except: pass

        # Filename where object was defined
        binary_file = False
        try:
            try:
                fname = inspect.getabsfile(obj)
            except TypeError:
                # For an instance, the file that matters is where its class was
                # declared.
                if hasattr(obj,'__class__'):
                    fname = inspect.getabsfile(obj.__class__)
            if fname.endswith('<string>'):
                fname = 'Dynamically generated function. No source code available.'
            if (fname.endswith('.so') or fname.endswith('.dll')):
                binary_file = True
            out.writeln(header('File:\t\t')+fname)
        except:
            # if anything goes wrong, we don't want to show source, so it's as
            # if the file was binary
            binary_file = True

        # reconstruct the function definition and print it:
        defln = self._getdef(obj,oname)
        if defln:
            out.write(header('Definition:\t')+self.format(defln))
 
        # Docstrings only in detail 0 mode, since source contains them (we
        # avoid repetitions).  If source fails, we add them back, see below.
        if ds and detail_level == 0:
                out.writeln(header('Docstring:\n') + indent(ds))
                
        # Original source code for any callable
        if detail_level:
            # Flush the source cache because inspect can return out-of-date
            # source
            linecache.checkcache()
            source_success = False
            try:
                try:
                    src = getsource(obj,binary_file)
                except TypeError:
                    if hasattr(obj,'__class__'):
                        src = getsource(obj.__class__,binary_file)
                if src is not None:
                    source = self.format(src)
                    out.write(header('Source:\n')+source.rstrip())
                    source_success = True
            except Exception, msg:
                pass
            
            if ds and not source_success:
                out.writeln(header('Docstring [source file open failed]:\n')
                            + indent(ds))

        # Constructor docstring for classes
        if inspect.isclass(obj):
            # reconstruct the function definition and print it:
            try:
                obj_init =  obj.__init__
            except AttributeError:
                init_def = init_ds = None
            else:
                init_def = self._getdef(obj_init,oname)
                init_ds  = getdoc(obj_init)
                # Skip Python's auto-generated docstrings
                if init_ds and \
                       init_ds.startswith('x.__init__(...) initializes'):
                    init_ds = None

            if init_def or init_ds:
                out.writeln(header('\nConstructor information:'))
                if init_def:
                    out.write(header('Definition:\t')+ self.format(init_def))
                if init_ds:
                    out.writeln(header('Docstring:\n') + indent(init_ds))
        # and class docstring for instances:
        elif obj_type is types.InstanceType or \
                 isinstance(obj,object):

            # First, check whether the instance docstring is identical to the
            # class one, and print it separately if they don't coincide.  In
            # most cases they will, but it's nice to print all the info for
            # objects which use instance-customized docstrings.
            if ds:
                try:
                    cls = getattr(obj,'__class__')
                except:
                    class_ds = None
                else:
                    class_ds = getdoc(cls)
                # Skip Python's auto-generated docstrings
                if class_ds and \
                       (class_ds.startswith('function(code, globals[,') or \
                   class_ds.startswith('instancemethod(function, instance,') or \
                   class_ds.startswith('module(name[,') ):
                    class_ds = None
                if class_ds and ds != class_ds:
                    out.writeln(header('Class Docstring:\n') +
                                indent(class_ds))

            # Next, try to show constructor docstrings
            try:
                init_ds = getdoc(obj.__init__)
                # Skip Python's auto-generated docstrings
                if init_ds and \
                       init_ds.startswith('x.__init__(...) initializes'):
                    init_ds = None
            except AttributeError:
                init_ds = None
            if init_ds:
                out.writeln(header('Constructor Docstring:\n') +
                            indent(init_ds))

            # Call form docstring for callable instances
            if hasattr(obj,'__call__'):
                #out.writeln(header('Callable:\t')+'Yes')
                call_def = self._getdef(obj.__call__,oname)
                #if call_def is None:
                #    out.writeln(header('Call def:\t')+
                #              'Calling definition not available.')
                if call_def is not None:
                    out.writeln(header('Call def:\t')+self.format(call_def))
                call_ds = getdoc(obj.__call__)
                # Skip Python's auto-generated docstrings
                if call_ds and call_ds.startswith('x.__call__(...) <==> x(...)'):
                    call_ds = None
                if call_ds:
                    out.writeln(header('Call docstring:\n') + indent(call_ds))

        # Finally send to printer/pager
        output = out.getvalue()
        if output:
            page.page(output)
        # end pinfo

    def info(self, obj, oname='', formatter=None, info=None, detail_level=0):
        """Compute a dict with detailed information about an object.

        Optional arguments:
        
        - oname: name of the variable pointing to the object.

        - formatter: special formatter for docstrings (see pdoc)

        - info: a structure with some information fields which may have been
        precomputed already.

        - detail_level: if set to 1, more information is given.
        """

        obj_type = type(obj)

        header = self.__head
        if info is None:
            ismagic = 0
            isalias = 0
            ospace = ''
        else:
            ismagic = info.ismagic
            isalias = info.isalias
            ospace = info.namespace

        # Get docstring, special-casing aliases:
        if isalias:
            if not callable(obj):
                try:
                    ds = "Alias to the system command:\n  %s" % obj[1]
                except:
                    ds = "Alias: " + str(obj)
            else:
                ds = "Alias to " + str(obj)
                if obj.__doc__:
                    ds += "\nDocstring:\n" + obj.__doc__
        else:
            ds = getdoc(obj)
            if ds is None:
                ds = '<no docstring>'
        if formatter is not None:
            ds = formatter(ds)

        # store output in a dict, we initialize it here and fill it as we go
        out = dict(name=oname, found=True, isalias=isalias, ismagic=ismagic)
        
        string_max = 200 # max size of strings to show (snipped if longer)
        shalf = int((string_max -5)/2)

        if ismagic:
            obj_type_name = 'Magic function'
        elif isalias:
            obj_type_name = 'System alias'
        else:
            obj_type_name = obj_type.__name__
        out['type_name'] = obj_type_name

        try:
            bclass = obj.__class__
            out['base_class'] = str(bclass)
        except: pass

        # String form, but snip if too long in ? form (full in ??)
        if detail_level >= self.str_detail_level:
            try:
                ostr = str(obj)
                str_head = 'string_form'
                if not detail_level and len(ostr)>string_max:
                    ostr = ostr[:shalf] + ' <...> ' + ostr[-shalf:]
                    ostr = ("\n" + " " * len(str_head.expandtabs())).\
                            join(q.strip() for q in ostr.split("\n"))
                if ostr.find('\n') > -1:
                    # Print multi-line strings starting at the next line.
                    str_sep = '\n'
                else:
                    str_sep = '\t'
                out[str_head] = ostr
            except:
                pass

        if ospace:
            out['namespace'] = ospace

        # Length (for strings and lists)
        try:
            out['length'] = str(len(obj))
        except: pass

        # Filename where object was defined
        binary_file = False
        try:
            try:
                fname = inspect.getabsfile(obj)
            except TypeError:
                # For an instance, the file that matters is where its class was
                # declared.
                if hasattr(obj,'__class__'):
                    fname = inspect.getabsfile(obj.__class__)
            if fname.endswith('<string>'):
                fname = 'Dynamically generated function. No source code available.'
            if (fname.endswith('.so') or fname.endswith('.dll')):
                binary_file = True
            out['file'] = fname
        except:
            # if anything goes wrong, we don't want to show source, so it's as
            # if the file was binary
            binary_file = True

        # reconstruct the function definition and print it:
        defln = self._getdef(obj, oname)
        if defln:
            out['definition'] = self.format(defln)

        # Docstrings only in detail 0 mode, since source contains them (we
        # avoid repetitions).  If source fails, we add them back, see below.
        if ds and detail_level == 0:
                out['docstring'] = ds
                
        # Original source code for any callable
        if detail_level:
            # Flush the source cache because inspect can return out-of-date
            # source
            linecache.checkcache()
            source_success = False
            try:
                try:
                    src = getsource(obj,binary_file)
                except TypeError:
                    if hasattr(obj,'__class__'):
                        src = getsource(obj.__class__,binary_file)
                if src is not None:
                    source = self.format(src)
                    out['source'] = source.rstrip()
                    source_success = True
            except Exception, msg:
                pass

        # Constructor docstring for classes
        if inspect.isclass(obj):
            # reconstruct the function definition and print it:
            try:
                obj_init =  obj.__init__
            except AttributeError:
                init_def = init_ds = None
            else:
                init_def = self._getdef(obj_init,oname)
                init_ds  = getdoc(obj_init)
                # Skip Python's auto-generated docstrings
                if init_ds and \
                       init_ds.startswith('x.__init__(...) initializes'):
                    init_ds = None

            if init_def or init_ds:
                if init_def:
                    out['init_definition'] = self.format(init_def)
                if init_ds:
                    out['init_docstring'] = init_ds

        # and class docstring for instances:
        elif obj_type is types.InstanceType or \
                 isinstance(obj, object):
            # First, check whether the instance docstring is identical to the
            # class one, and print it separately if they don't coincide.  In
            # most cases they will, but it's nice to print all the info for
            # objects which use instance-customized docstrings.
            if ds:
                try:
                    cls = getattr(obj,'__class__')
                except:
                    class_ds = None
                else:
                    class_ds = getdoc(cls)
                # Skip Python's auto-generated docstrings
                if class_ds and \
                       (class_ds.startswith('function(code, globals[,') or \
                   class_ds.startswith('instancemethod(function, instance,') or \
                   class_ds.startswith('module(name[,') ):
                    class_ds = None
                if class_ds and ds != class_ds:
                    out['class_docstring'] = class_ds

            # Next, try to show constructor docstrings
            try:
                init_ds = getdoc(obj.__init__)
                # Skip Python's auto-generated docstrings
                if init_ds and \
                       init_ds.startswith('x.__init__(...) initializes'):
                    init_ds = None
            except AttributeError:
                init_ds = None
            if init_ds:
                out['init_docstring'] = init_ds

            # Call form docstring for callable instances
            if hasattr(obj, '__call__'):
                call_def = self._getdef(obj.__call__, oname)
                if call_def is not None:
                    out['call_def'] = self.format(call_def)
                call_ds = getdoc(obj.__call__)
                # Skip Python's auto-generated docstrings
                if call_ds and call_ds.startswith('x.__call__(...) <==> x(...)'):
                    call_ds = None
                if call_ds:
                    out['call_docstring'] = call_ds

        # Compute the object's argspec as a callable.  The key is to decide
        # whether to pull it from the object itself, from its __init__ or
        # from its __call__ method.
        
        if inspect.isclass(obj):
            callable_obj = obj.__init__
        elif callable(obj):
            callable_obj = obj
        else:
            callable_obj = None

        if callable_obj:
            try:
                args,  varargs, varkw, defaults = getargspec(callable_obj)
            except (TypeError, AttributeError):
                # For extensions/builtins we can't retrieve the argspec
                pass
            else:
                out['argspec'] = dict(args=args, varargs=varargs,
                                      varkw=varkw, defaults=defaults)

        return object_info(**out)


    def psearch(self,pattern,ns_table,ns_search=[],
                ignore_case=False,show_all=False):
        """Search namespaces with wildcards for objects.

        Arguments:

        - pattern: string containing shell-like wildcards to use in namespace
        searches and optionally a type specification to narrow the search to
        objects of that type.

        - ns_table: dict of name->namespaces for search.

        Optional arguments:
        
          - ns_search: list of namespace names to include in search.

          - ignore_case(False): make the search case-insensitive.

          - show_all(False): show all names, including those starting with
          underscores.
        """
        #print 'ps pattern:<%r>' % pattern # dbg
        
        # defaults
        type_pattern = 'all'
        filter = ''

        cmds = pattern.split()
        len_cmds  =  len(cmds)
        if len_cmds == 1:
            # Only filter pattern given
            filter = cmds[0]
        elif len_cmds == 2:
            # Both filter and type specified
            filter,type_pattern = cmds
        else:
            raise ValueError('invalid argument string for psearch: <%s>' %
                             pattern)

        # filter search namespaces
        for name in ns_search:
            if name not in ns_table:
                raise ValueError('invalid namespace <%s>. Valid names: %s' %
                                 (name,ns_table.keys()))

        #print 'type_pattern:',type_pattern # dbg
        search_result = []
        for ns_name in ns_search:
            ns = ns_table[ns_name]
            tmp_res = list(list_namespace(ns,type_pattern,filter,
                                          ignore_case=ignore_case,
                                          show_all=show_all))
            search_result.extend(tmp_res)
        search_result.sort()

        page.page('\n'.join(search_result))
