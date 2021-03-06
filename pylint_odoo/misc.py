
import csv
import os
import re
import subprocess

from lxml import etree
from pylint.checkers import BaseChecker
from pylint.interfaces import IAstroidChecker
from restructuredtext_lint import lint_file as rst_lint

from . import settings


def get_plugin_msgs(pylint_run_res):
    """Get all message of this pylint plugin.
    :param pylint_run_res: Object returned by pylint.run method.
    :return: List of strings with message name.
    """
    all_plugin_msgs = [
        key
        for key in pylint_run_res.linter.msgs_store._messages
        if pylint_run_res.linter.msgs_store._messages[key].checker.name ==
        settings.CFG_SECTION
    ]
    return all_plugin_msgs


def get_sum_fails(pylint_stats):
    """Get a sum of all fails.
    :param pylint_stats: Object returned by pylint.run method.
    :return: Integer with sum of all errors found.
    """
    return sum([
        pylint_stats['by_msg'][msg]
        for msg in pylint_stats['by_msg']])


def join_node_args_kwargs(node):
    """Method to join args and keywords
    :param node: node to get args and keywords
    :return: List of args
    """
    args = (getattr(node, 'args', None) or []) + \
        (getattr(node, 'keywords', None) or [])
    return args


# TODO: Change all methods here

class WrapperModuleChecker(BaseChecker):

    __implements__ = IAstroidChecker

    node = None
    module_path = None
    msg_args = None
    msg_code = None
    msg_name_key = None
    odoo_node = None
    odoo_module_name = None
    manifest_file = None
    module = None

    def get_manifest_file(self, node_file):
        """Get manifest file path
        :param node_file: String with full path of a python module file.
        :return: Full path of manifest file if exists else return None"""
        if os.path.basename(node_file) == '__init__.py':
            for manifest_basename in settings.MANIFEST_FILES:
                manifest_file = os.path.join(
                    os.path.dirname(node_file), manifest_basename)
                if os.path.isfile(manifest_file):
                    return manifest_file

    def set_ext_files(self):
        """Create `self.ext_files` dictionary with {extension_file: [files]}
        """
        self.ext_files = {}
        for root, _, filenames in os.walk(self.module_path, followlinks=True):
            for filename in filenames:
                fext = os.path.splitext(filename)[1].lower()
                fname_rel = os.path.relpath(
                    os.path.join(root, filename), self.module_path)
                self.ext_files.setdefault(fext, []).append(fname_rel)

    def set_caches(self):
        # TODO: Validate if is a odoo module before and has checks enabled
        self.set_ext_files()

    def clear_caches(self):
        self.ext_files = None

    def leave_module(self, node):
        """Clear caches"""
        self.clear_caches()

    def open(self):
        self.odoo_node = None

    def wrapper_visit_module(self, node):
        """Call methods named with name-key from self.msgs
        Method should be named with next standard:
            def _check_{NAME_KEY}(self, module_path)
        by example: def _check_missing_icon(self, module_path)
                    to check missing-icon message name key
            And should return True if all fine else False.
        if a False is returned then add message of name-key.
        Assign object variables to use in methods.
        :param node: A astroid.scoped_nodes.Module
        :return: None
        """
        self.manifest_file = self.get_manifest_file(node.file)
        if self.manifest_file:
            self.odoo_node = node
            self.odoo_module_name = os.path.basename(
                os.path.dirname(self.odoo_node.file))
        elif self.odoo_node and \
                not os.path.dirname(self.odoo_node.file) in \
                os.path.dirname(node.file):
            self.odoo_node = None
            self.odoo_module_name = None
        self.node = node
        self.module_path = os.path.dirname(node.file)
        self.module = os.path.basename(self.module_path)
        self.set_caches()
        for msg_code, (title, name_key, description) in \
                sorted(self.msgs.iteritems()):
            self.msg_code = msg_code
            self.msg_name_key = name_key
            self.msg_args = None
            if not self.linter.is_message_enabled(msg_code):
                continue
            check_method = getattr(
                self, '_check_' + name_key.replace('-', '_'),
                None)
            is_odoo_check = self.manifest_file and \
                msg_code[1:3] == str(settings.BASE_OMODULE_ID)
            is_py_check = msg_code[1:3] == str(settings.BASE_PYMODULE_ID)
            if callable(check_method) and (is_odoo_check or is_py_check):
                if not check_method():
                    if not isinstance(self.msg_args, list):
                        self.msg_args = [self.msg_args]
                    for msg_args in self.msg_args:
                        node_file_original = node.file
                        node_lineno_original = node.lineno
                        msg_args_extra = self.set_extra_file(node, msg_args,
                                                             msg_code)
                        self.add_message(msg_code, line=node.lineno, node=node,
                                         args=msg_args_extra)
                        node.file = node_file_original
                        node.lineno = node_lineno_original

    def set_extra_file(self, node, msg_args, msg_code):
        if isinstance(msg_args, basestring):
            msg_args = (msg_args,)
        first_arg = msg_args and msg_args[0] or ""
        fregex_str = \
            r"(?P<file>^[\w|\-|\.|/ \\]+):?(?P<lineno>\d+)?:?(?P<colno>\d+)?"
        fregex = re.compile(fregex_str)
        fmatch = fregex.match(first_arg)
        msg = self.linter.msgs_store.check_message_id(msg_code).msg.\
            strip('"\' ')
        if not fmatch or not msg.startswith(r"%s"):
            return msg_args
        module_path = os.path.dirname(self.odoo_node.file)
        fname = fmatch.group('file')
        fpath = os.path.join(module_path, fname)
        node.file = fpath if os.path.isfile(fpath) else module_path
        node.lineno = int(fmatch.group('lineno') or 0)
        msg_strip = re.sub(fregex_str, '', first_arg, 1).strip(': ')
        return (msg_strip,) + msg_args[1:]

    def filter_files_ext(self, fext, relpath=True, skip_examples=True):
        """Filter files of odoo modules with a file extension.
        :param fext: Extension name of files to filter.
        :param relpath: Boolean to choose absolute path or relative path
                        If relpath is True then return relative paths
                        else return absolute paths
        :param skip_examples: Boolean to skip "examples" folder
        :return: List of paths of files matched
                 with extension fext.
        """
        dirnames_to_skip = []
        if skip_examples:
            dirnames_to_skip.extend(['example', 'examples', 'sample',
                                     'samples', 'lib'])
        if not fext.startswith('.'):
            fext = '.' + fext
        fext = fext.lower()
        fnames = self.ext_files.get(fext, [])
        for fname in list(fnames):
            dirnames = os.path.dirname(fname).split(os.sep)
            for dirname_to_skip in dirnames_to_skip:
                if dirname_to_skip in dirnames:
                    fnames.remove(fname)
                    break
        if not relpath:
            fnames = [
                os.path.join(self.module_path, fname)
                for fname in fnames]
        return fnames

    def check_rst_syntax(self, fname):
        """Check syntax in rst files.
        :param fname: String with file name path to check
        :return: Return list of errors.
        """
        return rst_lint(fname)

    def check_js_lint(self, fname):
        """Check javascript lint in fname.
        :param fname: String with full path of file to check
        :return: Return list of errors.
        """
        cmd = ['jshint', '--reporter=unix', fname]
        try:
            output = subprocess.Popen(
                cmd, stderr=subprocess.STDOUT,
                stdout=subprocess.PIPE).stdout.read()
        except OSError as oserr:
            output_err = ' - ' + cmd[0] + ': ' + oserr.strerror
            return [output_err]
        output = output.replace(fname, '')
        output_spplited = []
        if output:
            output_spplited.extend(
                output.strip('\n').split('\n')[:-2])
        return output_spplited

    def get_duplicated_items(self, items):
        """Get duplicated items
        :param items: Iterable items
        :return: List with tiems duplicated
        """
        unique_items = set()
        duplicated_items = set()
        for item in items:
            if item in unique_items:
                duplicated_items.add(item)
            else:
                unique_items.add(item)
        return list(duplicated_items)

    def parse_xml(self, xml_file):
        """Get xml parsed.
        :param xml_file: Path of file xml
        :return: Doc parsed (lxml.etree object)
            if there is syntax error return string error message
        """
        try:
            doc = etree.parse(open(xml_file))
        except etree.XMLSyntaxError as xmlsyntax_error_exception:
            return xmlsyntax_error_exception.message
        return doc

    def get_xml_records(self, xml_file, model=None):
        """Get tag `record` of a openerp xml file.
        :param xml_file: Path of file xml
        :param model: String with record model to filter.
                      if model is None then get all.
                      Default None.
        :return: List of lxml `record` nodes
            If there is syntax error return []
        """
        if model is None:
            model_filter = ''
        else:
            model_filter = "[@model='{model}']".format(model=model)
        doc = self.parse_xml(xml_file)
        return doc.xpath("/openerp//record" + model_filter) + \
            doc.xpath("/odoo//record" + model_filter) \
            if not isinstance(doc, basestring) else []

    def get_field_csv(self, csv_file, field='id'):
        """Get xml ids from csv file
        :param csv_file: Path of file csv
        :param field: Field to search
        :return: List of string with field rows
        """
        with open(csv_file, 'rb') as csvfile:
            lines = csv.DictReader(csvfile)
            return [line[field] for line in lines if field in line]

    def get_xml_redundant_module_name(self, xml_file, module=None):
        """Get xml redundant name module in xml_id of a openerp xml file
        :param xml_file: Path of file xml
        :param model: String with record model to filter.
                      if model is None then get all.
                      Default None.
        :return: List of tuples with (string, integer) with
            (module.xml_id, lineno) found
        """
        xml_ids = []
        for record in self.get_xml_records(xml_file):
            xml_module, xml_id = record.get('id').split('.') \
                if '.' in record.get('id') else ['', record.get('id')]
            if module and xml_module == module:
                xml_ids.append((xml_id, record.sourceline))
        return xml_ids
