import codecs
from copy import deepcopy
from datetime import datetime
import json
import logging
import os
import sys

import unicodecsv as csv

from DDR import TEMPLATE_EJSON
from DDR import TEMPLATE_METS
from DDR import natural_sort
from DDR import changelog
from DDR import commands
from DDR import dvcs
from DDR import models

COLLECTION_FILES_PREFIX = 'files'

# Some files' XMP data is wayyyyyy too big
csv.field_size_limit(sys.maxsize)
CSV_DELIMITER = ','
CSV_QUOTECHAR = '"'
CSV_QUOTING = csv.QUOTE_ALL


def dtfmt(dt, fmt='%Y-%m-%dT%H:%M:%S.%f'):
    """Format dates in consistent format.
    
    >>> dtfmt(datetime.fromtimestamp(0), fmt='%Y-%m-%dT%H:%M:%S.%f')
    '1969-12-31T16:00:00.000000'
    
    @param dt: datetime
    @param fmt: str Format string (default: '%Y-%m-%dT%H:%M:%S.%f')
    @returns: str
    """
    return dt.strftime(fmt)

def normalize_text(text):
    """Strip text, convert line endings, etc.
    
    TODO make this work on lists, dict values
    TODO handle those ^M chars
    
    >>> normalize_text('  this is a test')
    'this is a test'
    >>> normalize_text('this is a test  ')
    'this is a test'
    >>> normalize_text('this\r\nis a test')
    'this\\nis a test'
    >>> normalize_text('this\ris a test')
    'this\\nis a test'
    >>> normalize_text('this\\nis a test')
    'this\\nis a test'
    >>> normalize_text(['this is a test'])
    ['this is a test']
    >>> normalize_text({'this': 'is a test'})
    {'this': 'is a test'}
    """
    def process(t):
        try:
            t = t.strip()
            t = t.replace('\r\n', '\n').replace('\r', '\n').replace('\n', '\\n')
        except AttributeError:
            pass # doesn't work on ints and lists :P
        return t
    if isinstance(text, basestring):
        return process(text)
    return text

def csv_writer(csvfile):
    """Get a csv.writer object for the file.
    
    @param csvfile: A file object.
    """
    writer = csv.writer(
        csvfile,
        delimiter=CSV_DELIMITER,
        quoting=CSV_QUOTING,
        quotechar=CSV_QUOTECHAR,
    )
    return writer

def csv_reader(csvfile):
    """Get a csv.reader object for the file.
    
    @param csvfile: A file object.
    """
    reader = csv.reader(
        csvfile,
        delimiter=CSV_DELIMITER,
        quoting=CSV_QUOTING,
        quotechar=CSV_QUOTECHAR,
    )
    return reader

def write_csv(path, headers, rows):
    """Write header and list of rows to file.
    
    >>> path = '/tmp/batch-test_write_csv.csv'
    >>> headers = ['id', 'title', 'description']
    >>> rows = [
    ...     ['ddr-test-123', 'thing 1', 'nothing here'],
    ...     ['ddr-test-124', 'thing 2', 'still nothing'],
    ... ]
    >>> batch.write_csv(path, headers, rows)
    >>> with open(path, 'r') as f:
    ...    f.read()
    '"id","title","description"\r\n"ddr-test-123","thing 1","nothing here"\r\n"ddr-test-124","thing 2","still nothing"\r\n'
    
    @param path: Absolute path to CSV file
    @param headers: list of strings
    @param rows: list of lists
    """
    with codecs.open(path, 'wb', 'utf-8') as f:
        writer = csv_writer(f)
        writer.writerow(headers)
        for row in rows:
            writer.writerow(row)

def read_csv(path):
    """Read specified file, return list of rows.
    
    >>> path = '/tmp/batch-test_write_csv.csv'
    >>> csv_file = '"id","title","description"\r\n"ddr-test-123","thing 1","nothing here"\r\n"ddr-test-124","thing 2","still nothing"\r\n'
    >>> with open(path, 'w') as f:
    ...    f.write(csv_file)
    >>> batch.read_csv(path)
    [
        ['id', 'title', 'description'],
        ['ddr-test-123', 'thing 1', 'nothing here'],
        ['ddr-test-124', 'thing 2', 'still nothing']
    ]
    
    @param path: Absolute path to CSV file
    @returns list of rows
    """
    rows = []
    with codecs.open(path, 'rU', 'utf-8') as f:  # the 'U' is for universal-newline mode
        reader = csv_reader(f)
        for row in reader:
            rows.append(row)
    return rows

def make_entity_path(collection_path, entity_id):
    """Returns path to entity directory.
    
    >>> cpath0 = '/var/www/media/base/ddr-test-123'
    >>> eid0 = 'ddr-test-123-456'
    >>> make_entity_path(cpath0, eid0)
    '/var/www/media/base/ddr-test-123/files/ddr-test-123-456'
    
    @param collection_path: str
    @param entity_id: str
    @returns: str Absolute path to entity.
    """
    return os.path.join(collection_path, COLLECTION_FILES_PREFIX, entity_id)

def make_entity_json_path(collection_path, entity_id):
    """Returns path to entity JSON file.
    
    >>> cpath0 = '/var/www/media/base/ddr-test-123'
    >>> eid0 = 'ddr-test-123-456'
    >>> make_entity_json_path(cpath0, eid0)
    '/var/www/media/base/ddr-test-123/files/ddr-test-123-456/entity.json'
    
    @param collection_path: str
    @param entity_id: str
    @returns: str Absolute path to entity JSON.
    """
    return os.path.join(collection_path, COLLECTION_FILES_PREFIX, entity_id, 'entity.json')


# export ---------------------------------------------------------------

def make_tmpdir(tmpdir):
    """Make tmp dir if doesn't exist.
    
    @param tmpdir: Absolute path to dir
    """
    if not os.path.exists(tmpdir):
        os.makedirs(tmpdir)

def module_field_names(module):
    """Manipulates list of fieldnames to include/exclude columns from CSV.
    
    >>> m = TestModule()
    >>> m.FIELDS = [{'name':'id'}, {'name':'title'}, {'name':'description'}]
    >>> m.FIELDS_CSV_EXCLUDED = ['description']
    >>> m.MODEL = 'collection'
    >>> batch.module_field_names(m)
    ['id', 'title']
    >>> m.MODEL = 'entity'
    >>> batch.module_field_names(m)
    ['id', 'title']
    >>> m.MODEL = 'file'
    >>> batch.module_field_names(m)
    ['file_id', 'id', 'title']
    
    @param module: 
    @returns: list of field names
    """
    if hasattr(module, 'FIELDS_CSV_EXCLUDED'):
        excluded = module.FIELDS_CSV_EXCLUDED
    else:
        excluded = []
    fields = []
    for field in module.FIELDS:
        if not field['name'] in excluded:
            fields.append(field['name'])
    if module.MODEL == 'collection':
        pass
    elif module.MODEL == 'entity':
        pass
    elif module.MODEL == 'file':
        fields.insert(0, 'file_id')
    return fields

def dump_object(obj, module, field_names):
    """Dump object field values to list.
    
    Note: Autogenerated and non-user-editable fields
    (SHA1 and other hashes, file size, etc) should be excluded
    from the CSV file.
    Note: For files these are replaced by File.id which contains
    the role and a fragment of the SHA1 hash.
    
    @param obj_
    @param module: 
    @param field_names: 
    @returns: list of values
    """
    # seealso ddrlocal.models.__init__.module_function()
    values = []
    for field_name in field_names:
        value = ''
        # insert file_id as first column
        if (module.MODEL == 'file') and (field_name == 'file_id'):
            val = obj.id
        elif hasattr(obj, field_name):
            # run csvdump_* functions on field data if present
            val = models.module_function(
                module,
                'csvdump_%s' % field_name,
                getattr(obj, field_name)
            )
            if val == None:
                val = ''
        value = normalize_text(val)
        values.append(value)
    return values

def export(json_paths, class_, module, csv_path):
    """Write the specified objects' data to CSV.
    
    # entities
    collection_path = '/var/www/media/base/ddr-test-123'
    entity_paths = []
    for path in models.metadata_files(basedir=collection_path, recursive=True):
        if os.path.basename(path) == 'entity.json':
            entity_paths.append(path)
    csv_path = '/tmp/ddr-test-123-entities.csv'
    export(entity_paths, entity_module, csv_path)
    
    # files
    collection_path = '/var/www/media/base/ddr-test-123'
    file_paths = []
    for path in models.metadata_files(basedir=collection_path, recursive=True):
        if ('master' in path) or ('mezzanine' in path):
            file_paths.append(path)
    csv_path = '/tmp/ddr-test-123-files.csv'
    export(file_paths, files_module, csv_path)

    @param json_paths: list of .json files
    @param class_: subclass of Entity or File
    @param module: entity_module or files_module
    @param csv_path: Absolute path to CSV data file.
    """
    if module.MODEL == 'file':
        json_paths = models.sort_file_paths(json_paths)
    else:
        json_paths = natural_sort(json_paths)
    make_tmpdir(os.path.dirname(csv_path))
    field_names = module_field_names(module)
    with codecs.open(csv_path, 'wb', 'utf-8') as csvfile:
        writer = csv_writer(csvfile)
        writer.writerow(field_names)
        for n,path in enumerate(json_paths):
            if module.MODEL == 'entity':
                obj = class_.from_json(os.path.dirname(path))
            elif module.MODEL == 'file':
                obj = class_.from_json(path)
            logging.info('%s/%s - %s' % (n+1, len(json_paths), obj.id))
            writer.writerow(dump_object(obj, module, field_names))
    return csv_path


# update entities ------------------------------------------------------

def get_required_fields(fields, exceptions):
    """Picks out the required fields.
    
    >>> fields = [
    ...     {'name':'id', 'form':{'required':True}},
    ...     {'name':'title', 'form':{'required':True}},
    ...     {'name':'description', 'form':{'required':False}},
    ...     {'name':'formless'},
    ...     {'name':'files', 'form':{'required':True}},
    ... ]
    >>> exceptions = ['files', 'whatever']
    >>> batch.get_required_fields(fields, exceptions)
    ['id', 'title']
    
    @param fields: module.FIELDS
    @param exceptions: list of field names
    @returns: list of field names
    """
    required_fields = []
    for field in fields:
        if field.get('form', None) and field['form']['required'] and (field['name'] not in exceptions):
            required_fields.append(field['name'])
    return required_fields

def load_vocab_files(vocabs_path):
    """Loads FIELD.json files in the 'ddr' repository
    
    @param vocabs_path: Absolute path to dir containing vocab .json files.
    @returns: list of raw text contents of files.
    """
    json_paths = []
    for p in os.listdir(vocabs_path):
        path = os.path.join(vocabs_path, p)
        if os.path.splitext(path)[1] == '.json':
            json_paths.append(path)
    files = []
    for path in json_paths:
        with codecs.open(path, 'r', 'utf-8') as f:
            files.append(f.read())
    return files

def prep_valid_values(json_texts):
    """Packages dict of acceptable values for controlled-vocab fields.
    
    Loads choice values from FIELD.json files in the 'ddr' repository
    into a dict:
    {
        'FIELD': ['VALID', 'VALUES', ...],
        'status': ['inprocess', 'completed'],
        'rights': ['cc', 'nocc', 'pdm'],
        ...
    }
    
    >>> json_texts = [
    ...     '{"terms": [{"id": "advertisement"}, {"id": "album"}, {"id": "architecture"}], "id": "genre"}',
    ...     '{"terms": [{"id": "eng"}, {"id": "jpn"}, {"id": "chi"}], "id": "language"}',
    ... ]
    >>> batch.prep_valid_values(json_texts)
    {u'genre': [u'advertisement', u'album', u'architecture'], u'language': [u'eng', u'jpn', u'chi']}
    
    @param json_texts: list of raw text contents of files.
    @returns: dict
    """
    valid_values = {}
    for text in json_texts:
        data = json.loads(text)
        field = data['id']
        values = [term['id'] for term in data['terms']]
        if values:
            valid_values[field] = values
    return valid_values

def make_row_dict(headers, row):
    """Turns the row into a dict with the headers as keys
    
    >>> headers0 = ['id', 'created', 'lastmod', 'title', 'description']
    >>> row0 = ['id', 'then', 'now', 'title', 'descr']
    {'title': 'title', 'description': 'descr', 'lastmod': 'now', 'id': 'id', 'created': 'then'}

    @param headers: List of header field names
    @param row: A single row (list of fields, not dict)
    @returns dict
    """
    if len(headers) != len(row):
        logging.error(headers)
        logging.error(row)
        raise Exception('Row and header have different number of fields.')
    d = {}
    for n in range(0, len(row)):
        d[headers[n]] = row[n]
    return d

def validate_headers(model, headers, field_names, exceptions):
    """Validates headers and crashes if problems.
    
    >>> model = 'entity'
    >>> field_names = ['id', 'title', 'notused']
    >>> exceptions = ['notused']
    >>> headers = ['id', 'title']
    >>> validate_headers(model, headers, field_names, exceptions)
    >>> headers = ['id', 'titl']
    >>> validate_headers(model, headers, field_names, exceptions)
    Traceback (most recent call last):
      File "<input>", line 1, in <module>
      File "/usr/local/lib/python2.7/dist-packages/DDR/batch.py", line 319, in validate_headers
        raise Exception('MISSING HEADER(S): %s' % missing_headers)
    Exception: MISSING HEADER(S): ['title']
    
    @param model: 'entity' or 'file'
    @param headers: List of field names
    @param field_names: List of field names
    @param exceptions: List of nonrequired field names
    """
    logging.info('Validating headers')
    headers = deepcopy(headers)
    # validate
    missing_headers = []
    for field in field_names:
        if (field not in exceptions) and (field not in headers):
            missing_headers.append(field)
    if missing_headers:
        raise Exception('MISSING HEADER(S): %s' % missing_headers)
    bad_headers = []
    for header in headers:
        if header not in field_names:
            bad_headers.append(header)
    if bad_headers:
        raise Exception('BAD HEADER(S): %s' % bad_headers)

def account_row(required_fields, rowd):
    """Returns list of any required fields that are missing from rowd.
    
    >>> required_fields = ['id', 'title']
    >>> rowd = {'id': 123, 'title': 'title'}
    >>> account_row(required_fields, rowd)
    []
    >>> required_fields = ['id', 'title', 'description']
    >>> account_row(required_fields, rowd)
    ['description']
    
    @param required_fields: List of required field names
    @param rowd: A single row (dict, not list of fields)
    @returns: list of field names
    """
    missing = []
    for f in required_fields:
        if (f not in rowd.keys()) or (not rowd.get(f,None)):
            missing.append(f)
    return missing

def validate_row(module, headers, valid_values, rowd):
    """Examines row values and returns names of invalid fields.
    
    TODO refers to lots of globals!!!
    
    @param module: entity_module or files_module
    @param headers: List of field names
    @param valid_values:
    @param rowd: A single row (dict, not list of fields)
    @returns: list of invalid values
    """
    invalid = []
    for field in headers:
        value = models.module_function(
            module,
            'csvload_%s' % field,
            rowd[field]
        )
        valid = models.module_function(
            module,
            'csvvalidate_%s' % field,
            [valid_values, value]
        )
        if not valid:
            invalid.append(field)
    return invalid

def validate_rows(module, headers, required_fields, valid_values, rows):
    """Examines rows and crashes if problems.
    
    @param module: entity_module or files_module
    @param headers: List of field names
    @param required_fields: List of required field names
    @param valid_values:
    @param rows: List of rows (each with list of fields, not dict)
    """
    logging.info('Validating rows')
    for n,row in enumerate(rows):
        rowd = make_row_dict(headers, row)
        missing_required = account_row(required_fields, rowd)
        invalid_fields = validate_row(module, headers, valid_values, rowd)
        # print feedback and die
        if missing_required or invalid_fields:
            if missing_required:
                raise Exception('Row %s missing required fields: %s' % (n+1, missing_required))
            if invalid_fields:
                for field in invalid_fields:
                    logging.error('row%s:%s = "%s"' % (n+1, field, rowd[field]))
                    logging.error('valid values: %s' % valid_values[field])
                raise Exception('Row %s invalid values: %s' % (n+1, invalid_fields))

def load_entity(collection_path, class_, rowd):
    """Get new or existing Entity object
    
    @param collection_path: Absolute path to collection
    @param class_: subclass of Entity
    @param rowd:
    @returns: entity
    """
    entity_uid = rowd['id']
    entity_path = make_entity_path(collection_path, entity_uid)
    entity_json_path = make_entity_json_path(collection_path, entity_uid)
    # update an existing entity
    if os.path.exists(entity_json_path):
        entity = class_.from_json(entity_path)
        entity.new = False
    else:
        entity = class_(entity_path)
        entity.id = entity_uid
        entity.record_created = datetime.now()
        entity.record_lastmod = datetime.now()
        entity.files = []
        entity.new = True
    return entity

def csvload_entity(entity, module, field_names, rowd):
    """Update entity with values from CSV row.
    
    TODO Populates entity attribs EXCEPT FOR .files!!!
    
    @param entity:
    @param module: entity_module
    @param field_names:
    @param rowd:
    @returns: entity,modified
    """
    # run csvload_* functions on row data, set values
    entity.modified = 0
    for field in field_names:
        oldvalue = getattr(entity, field, '')
        value = models.module_function(
            module,
            'csvload_%s' % field,
            rowd[field]
        )
        value = normalize_text(value)
        if value != oldvalue:
            entity.modified += 1
        setattr(entity, field, value)
    if entity.modified:
        entity.record_lastmod = datetime.now()
    return entity

def write_entity_changelog(entity, git_name, git_mail, agent):
    if entity.new:
        msg = 'Initialized entity {}'
    else:
        msg = 'Updated entity file {}'
    messages = [
        msg.format(entity.json_path),
        '@agent: %s' % agent,
    ]
    changelog.write_changelog_entry(
        entity.changelog_path, messages,
        user=git_name, email=git_mail)

def update_entities(csv_path, collection_path, class_, module, vocabs_path, git_name, git_mail, agent):
    """Reads a CSV file, checks for errors, and writes entity.json files
    
    This function writes and stages files but does not commit them!
    That is left to the user or to another function.
    
    TODO What if entities already exist???
    TODO do we overwrite fields?
    TODO how to handle excluded fields like XMP???
    
    @param csv_path: Absolute path to CSV data file.
    @param collection_path: Absolute path to collection repo.
    @param class_: subclass of Entity
    @param module: entity_module
    @param vocabs_path: Absolute path to vocab dir
    @param git_name:
    @param git_mail:
    @param agent:
    @returns: list of updated entities
    """
    field_names = module_field_names(module)
    nonrequired_fields = module.REQUIRED_FIELDS_EXCEPTIONS
    required_fields = get_required_fields(module.FIELDS, nonrequired_fields)
    valid_values = prep_valid_values(load_vocab_files(vocabs_path))
    # read entire file into memory
    rows = read_csv(csv_path)
    headers = rows.pop(0)
    # check for errors
    validate_headers('entity', headers, field_names, nonrequired_fields)
    validate_rows(module, headers, required_fields, valid_values, rows)
    # ok go
    git_files = []
    annex_files = []
    updated = []
    for n,row in enumerate(rows):
        rowd = make_row_dict(headers, row)
        logging.info('%s/%s - %s' % (n+1, len(rows), rowd['id']))
        entity = load_entity(collection_path, class_, rowd)
        entity = csvload_entity(entity, module, field_names, rowd)
        if entity.new or entity.modified:
            if not os.path.exists(entity.path):
                os.mkdir(entity.path)
            logging.debug('    writing %s' % entity.json_path)
            entity.write_json()
            write_entity_changelog(entity, git_name, git_mail, agent)
            git_files.append(entity.json_path_rel)
            git_files.append(entity.changelog_path_rel)
            updated.append(entity)
    # stage modified files
    logging.info('Staging changes to the repo')
    repo = dvcs.repository(collection_path)
    logging.debug(str(repo))
    for path in git_files:
        logging.debug('git add %s' % path)
        repo.git.add(path)
    return updated

# update files ---------------------------------------------------------

class ModifiedFilesError(Exception):
    pass

class UncommittedFilesError(Exception):
    pass

def test_repository(repo):
    """Raise exception if staged or modified files in repo
    
    Entity.add_files will not work properly if the repo contains staged
    or modified files.
    
    @param repo: GitPython repository
    """
    logging.info('Checking repository')
    staged = dvcs.list_staged(repo)
    if staged:
        logging.error('*** Staged files in repo %s' % repo.working_dir)
        for f in staged:
            logging.error('*** %s' % f)
        raise UncommittedFilesError('Repository contains staged/uncommitted files - import cancelled!')
    modified = dvcs.list_modified(repo)
    if modified:
        logging.error('Modified files in repo: %s' % repo.working_dir)
        for f in modified:
            logging.error('*** %s' % f)
        raise ModifiedFilesError('Repository contains modified files - import cancelled!')

def test_entities(collection_path, class_, rowds):
    """Test-loads Entities mentioned in rows; crashes if any are missing.
    
    When files are being updated/added, it's important that all the parent
    entities already exist.
    
    @param collection_path:
    @param rowds: List of rowds
    @param class_: subclass of Entity
    @returns: ok,bad
    """
    logging.info('Validating parent entities')
    basedir = os.path.dirname(os.path.dirname(collection_path))
    # get unique entity_ids
    eids = []
    for rowd in rowds:
        fid = models.split_object_id(rowd['file_id'])
        model,repo,org,cid,eid = fid[:5]
        entity_id = models.make_object_id('entity', repo,org,cid,eid)
        eids.append(entity_id)
    # test-load the Entities
    entities = {}
    bad = []
    for entity_id in eids:
        entity_path = make_entity_path(collection_path, entity_id)
        # update an existing entity
        entity = None
        if os.path.exists(entity_path):
            entity = class_.from_json(entity_path)
        if entity:
            entities[entity.id] = entity
        else:
            bad.append(entity_id)
    if bad:
        logging.error('One or more entities could not be loaded! - IMPORT CANCELLED!')
        for f in bad:
            logging.error('    %s' % f)
    if bad:
        raise Exception('Cannot continue!')
    return entities

def test_new_files(csv_path, rowds):
    """Finds new files in CSV, indicates which are ok, which are missing.
    
    Files to be imported must be located in the same directory as the CSV file.
    
    @param csv_path: Absolute path to CSV file
    @param rowds: List of rowds
    @returns: ok,bad - lists of valid and invalid paths
    """
    logging.info('Checking for new files')
    paths = []
    for rowd in rowds:
        fid = models.split_object_id(rowd['file_id'])
        if len(fid) == 6:
            paths.append(os.path.join(
                os.path.dirname(csv_path),
                rowd['basename_orig']
            ))
    ok = []
    bad = []
    for path in paths:
        if os.path.exists(path):
            ok.append(path)
        else:
            bad.append(path)
    if ok:
        logging.debug('| %s new files' % len(ok))
        for f in ok:
            logging.debug('| %s' % f)
    if bad:
        logging.error('*** One or more new files could not be located! - IMPORT CANCELLED!')
        for f in bad:
            logging.error('*** %s' % f)
        raise Exception('Cannot continue!')

def load_file(collection_path, file_class, rowd):
    """Loads Entity from JSON file or creates fresh one.
    
    @param collection_path: Absolute path to collection
    @param file_class: subclass of DDRFile
    @param rowd: dict containing file fields:values
    @returns: DDRLocalFile object
    """
    if rowd.get('file_id',None):
        file_path = models.path_from_id(
            rowd['file_id'],
            os.path.dirname(collection_path)
        )
        if file_path:
            file_path = file_path + '.json'
    # update an existing file
    if file_path and os.path.exists(file_path):
        file_ = file_class.from_json(file_path)
        file_.exists = True
    else:
        file_ = file_class()
        file_.exists = False
    return file_

def csvload_file(file_, module, field_names, rowd):
    """Loads file data from CSV row and convert to Python data
    
    @param file_: DDRLocalFile object
    @param module: file_module
    @param field_names: list of field names
    @param rowd: dict containing file fields:values
    @returns: DDRLocalFile object
    """
    # run csvload_* functions on row data, set values
    file_.modified = 0
    for field in field_names:
        oldvalue = getattr(file_, field, '')
        value = models.module_function(
            module,
            'csvload_%s' % field,
            rowd[field]
        )
        value = normalize_text(value)
        if value != oldvalue:
            file_.modified += 1
        setattr(file_, field, value)
    return file_

def write_file_changelogs(entities, git_name, git_mail, agent):
    """Writes entity update/add changelogs, returns list of changelog paths
    
    Assembles appropriate changelog messages and then updates changelog for
    each entity.  update_files() adds lists of updated and added File objects
    to entities in list.
    
    TODO should this go in DDR.changelog.py?
    
    @param entities: list of Entity objects.
    @param git_name:
    @param git_mail:
    @param agent:
    @returns: list of paths relative to repository base
    """
    git_files = []
    for entity in entities:
        messages = []
        if getattr(entity, 'changelog_updated', None):
            for f in entity.changelog_updated:
                messages.append('Updated entity file {}'.format(f.json_path_rel))
        if getattr(entity, 'changelog_added', None):
            for f in entity.changelog_added:
                messages.append('Added entity file {}'.format(f.json_path_rel))
        messages.append('@agent: %s' % agent)
        changelog.write_changelog_entry(
            entity.changelog_path,
            messages,
            user=git_name,
            email=git_mail)
        git_files.append(entity.changelog_path_rel)
    return git_files

def update_files(csv_path, collection_path, entity_class, file_class, module, vocabs_path, git_name, git_mail, agent):
    """Updates metadata for files in csv_path.
    
    
    
    TODO how to handle excluded fields like XMP???
    
    @param csv_path: Absolute path to CSV data file.
    @param collection_path: Absolute path to collection repo.
    @param entity_class: subclass of Entity
    @param file_class: subclass of DDRFile
    @param module: file_module
    @param vocabs_path: Absolute path to vocab dir
    @param git_name:
    @param git_mail:
    @param agent:
    """
    logging.info('-----------------------------------------------')
    csv_dir = os.path.dirname(csv_path)
    field_names = module_field_names(module)
    nonrequired_fields = module.REQUIRED_FIELDS_EXCEPTIONS
    required_fields = get_required_fields(module.FIELDS, nonrequired_fields)
    valid_values = prep_valid_values(load_vocab_files(vocabs_path))
    # read entire file into memory
    logging.info('Reading %s' % csv_path)
    rows = read_csv(csv_path)
    headers = rows.pop(0)
    logging.info('%s rows' % len(rows))
    
    # check for problems before we go through the main loop
    validate_headers('file', headers, field_names, nonrequired_fields)
    validate_rows(module, headers, required_fields, valid_values, rows)
    # make list-of-dicts
    rowds = []
    while rows:
        rowd = rows.pop(0)
        rowds.append(make_row_dict(headers, rowd))
    # more checks
    repository = dvcs.repository(collection_path)
    logging.debug(repository)
    test_repository(repository)
    entities = test_entities(collection_path, entity_class, rowds)
    test_new_files(csv_path, rowds)
    
    logging.info('Updating/Adding - - - - - - - - - - - - - - - -')
    git_files = []
    for entity in entities.itervalues():
        entity.changelog_updated = []
        entity.changelog_added = []
    for n,rowd in enumerate(rowds):
        logging.info('| %s/%s - %s' % (n+1, len(rowds), rowd['file_id']))
        file_ = load_file(collection_path, file_class, rowd)
        file_ = csvload_file(file_, module, field_names, rowd)
        if file_.exists:
            # update metadata
            entity_id = models.id_from_path(os.path.join(file_.entity_path, 'entity.json'))
            entity = entities[entity_id]
            file_.write_json()
            git_files.append(file_.json_path_rel)
            entity.changelog_updated.append(file_)
            
        else:
            # add new file
            src_path = os.path.join(os.path.dirname(csv_path), rowd['basename_orig'])
            logging.info('    %s' % src_path)
            # have to make our own file_id/entity_id
            model,repo,org,cid,eid,role = models.split_object_id(rowd['file_id'])
            entity = entities[models.make_object_id('entity', repo,org,cid,eid)]
            logging.info('    log %s' % entity.addfile_logger().logpath)
            # add the file
            file_,filerepo,filelog = entity.add_file(
                src_path, role, rowd, git_name, git_mail, agent)
            logging.info('  %s/%s - %s < %s' % (
                n+1, len(rowds), file_.id, file_.basename_orig))
            # file_add stages files, don't need to use git_add
            entity.changelog_added.append(file_)
    
    logging.info('Writing entity changelogs')
    git_files += write_file_changelogs(
        [e for e in entities.itervalues()],
        git_name, git_mail, agent)
    
    # stage git_files
    logging.info('Staging files to the repo')
    for path in git_files:
        repository.git.add(path)
    for path in natural_sort(dvcs.list_staged(repository)):
        if path in git_files:
            logging.debug('| %s' % path)
        else:
            logging.debug('+ %s' % path)