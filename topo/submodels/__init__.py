"""
A set of tools which allow specifying a model consisting of sheets
organized in levels, and projections connecting these sheets. The
sheets have an attribute matchconditions allowing to specify which
other (incoming) sheets a sheet should connect to.

Instances of the LabelDecorator decorator are offered for setting
parameters/matchconditions for a sheet within a level, as well as
parameters for projections.
"""

import os
import sys
import itertools

from functools import wraps
from contextlib import contextmanager
from collections import OrderedDict

import param
import lancet
import topo

from dataviews.collector import AttrTree
from topo.misc.commandline import global_params


@contextmanager
def stdout_redirected(new_stdout):
    """
    Example taken from PEP 343. Used to temporarily silence param
    warnings about setting non-parameter class attributes.
    """
    save_stdout = sys.stdout
    sys.stdout = new_stdout
    try:
        yield None
    finally:
        sys.stdout = save_stdout


class Specification(object):
    """
    Specifications are templates for sheet or projection objects which
    may be resolved to the corresponding simulation object once
    instantiated.

    All specifications have the following attribute:

    :'parameters': Keyword argument dictionary specifying which
    parameters should be passed to the sheet or projection object.
    """

    def update_parameters(self, params):
        self.parameters.update(params)


    def resolve(self):
        """
        Returns the object in topo.sim corresponding to the string
        name of this object, typically a Sheet or a Projection.

        The appropriate object must be instantiated in topo.sim first.
        """
        from topo import sim # pyflakes:ignore (needed for eval)
        return eval('sim.'+str(self))


    def __init__(self, object_type):
        self.parameters = {}
        for param_name, default_value in object_type.params().items():
            self.parameters[param_name]=default_value.default



class SheetSpec(Specification):
    """
    SheetSpec acts as a template for sheet objects.
    """

    name_ordering = ['eye','level', 'cone', 'polarity',
                     'SF','opponent','surround']

    @property
    def level(self):
        return self.properties['level']


    def __init__(self, sheet_type, properties):
        """
        Initialize a SheetSpec object of a certain Sheet type with the
        given properties.

       :'sheet_type': Subclass of topo.base.sheet.Sheet.
       :'properties': Dictionary specifying the properties of the
       sheet. There must be a value given for the key 'level'.
        """
        super(SheetSpec,self).__init__(sheet_type)

        if 'level' not in properties:
            raise Exception("SheetSpec always requires 'level' property.")


        properties = [(k, properties[k]) for k in self.name_ordering
                      if k in properties]

        self.sheet_type = sheet_type
        self.properties = OrderedDict(properties)


    def __call__(self):
        """
        Instantiate the sheet and register it in topo.sim.
        """
        topo.sim[str(self)]=self.sheet_type(**self.parameters)


    def __str__(self):
        """
        Returns a string representation of the SheetSpec from the
        properties values.
        """
        name=''
        for prop in self.properties.itervalues():
            name+=str(prop)

        return name

    def __repr__(self):
        type_name = self._sheet_type.__name__
        properties_repr = ', '.join("%r:%r" % (k,v) for (k,v)
                                    in self.properties.items())
        return "SheetSpec(%s, {%s})" % (type_name, properties_repr)



class ProjectionSpec(Specification):
    """
    ProjectionSpec acts as a template for projection objects.
    """

    def __init__(self, projection_type, src, dest):
        """
        Initialize a ProjectionSpec object of a certain Projection
        type with the given src and dest SheetSpecs.

       :'projection_type': Subclass of topo.base.projection.Projection
       :'src': SheetSpec of the source sheet
       :'dest': SheetSpec of the destination sheet
        """
        super(ProjectionSpec, self).__init__(projection_type)

        self.projection_type = projection_type
        self.src = src
        self.dest = dest

        # These parameters are directly passed into topo.sim.connect()!
        ignored_keys = ['src', 'dest']
        self.parameters = dict((k,v) for (k,v) in self.parameters.items()
                               if k not in ignored_keys)

    def __call__(self):
        """
        Instantiate the projection and register it in topo.sim.
        """
        topo.sim.connect(str(self.src),str(self.dest),
                         self.projection_type,
                         **self.parameters)

    def __str__(self):
        return str(self.dest)+'.'+self.parameters['name']


    def __repr__(self):
        type_name = self._projection_type.__name__
        return "ProjectionSpec(%s, %r, %r)" % (type_name, self.src, self.dest)



class ObjectClass(object):
    """
    Decorator class which can be instantiated to create a decorator
    object to annotate method with a certain type.

    After decorating several methods or functions, the dictionary of
    the decorated callables may be accessed via the labels
    attribute. Object types are accessible via the types attribute.
    """
    def __init__(self, name, object_type):
        self.name = name
        self.labels = {}
        self.types = {}
        self.type = object_type

        # Enable IPython tab completion in the settings method
        kwarg_string = ", ".join("%s=%s" % (name, type(p.default))
                                 for (name, p) in object_type.params().items())
        self.settings.__func__.__doc__ =  'settings(%s)' % kwarg_string


    def settings(self, **kwargs):
        """
        A convenient way of generating parameter dictionaries with
        tab-completion in IPython.
        """
        return kwargs


    def __call__(self, f):
        label = f.__name__
        @wraps(f)
        def inner(*args, **kwargs):
            return f(*args, **kwargs)

        self.types[label] = self.type
        self.labels[label] = inner
        return inner


    def __repr__(self):
        return "ObjectClass(%s, %s)" % (self.name, self.type.name)



class MatchConditions(object):
    """
    Decorator class for matchconditions.
    """
    def __init__(self):
        self._levels = {}


    def compute_conditions(self, level, model, properties):
        """
        Collect the matchcondition dictionary for a particular level
        given a certain Model instance and sheet properties.
        """
        if level not in self:
            raise Exception("No level %r defined" % level)
        return dict((k, fn(model, properties))
                     for (k, fn) in self._levels[level].items())


    def __call__(self, level):
        def decorator(f):
            condition_name = f.__name__
            @wraps(f)
            def inner(self, *args, **kwargs):
                return f(self, *args, **kwargs)

            if level not in self._levels:
                self._levels[level] = {condition_name:inner}
            else:
                self._levels[level][condition_name] = inner
            return inner
        return decorator

    def __repr__(self):
        return "MatchConditions()"

    def __contains__(self, key):
        return key in self._levels



class Model(param.Parameterized):
    """
    The available setup options are:

        :'training_patterns': fills the training_patterns AttrTree
        with pattern generator instances. The path is the name of the
        input sheet. Usually calls PatternCoordinator to do this.
        :'setup_sheets': determines the number of sheets, their types
        and names sets sheet parameters according to the registered
        methods in level sets sheet matchconditions according to the
        registered methods in matchconditions
        :'projections': determines which connections should be present
        between the sheets according to the matchconditions of
        SheetSpec objects, using connect to specify the
        connection type and sets their parameters according to the
        registered methods in connect


    The available instantiate options are:

        :'sheets': instantiates all sheets and registers them in
        topo.sim
        :'projections': instantiates all projections and registers
        them in topo.sim
    """
    __abstract = True

    matchconditions = MatchConditions()

    sheet_decorators = set()
    projection_decorators = set()

    @classmethod
    def register_decorator(cls, object_type):
        name = object_type.name.lower()
        decorator = ObjectClass(name, object_type)
        setattr(cls, name,  decorator)

        if issubclass(object_type, topo.sheet.Sheet):
            cls.sheet_decorators.add(decorator)
        if issubclass(object_type, topo.projection.Projection):
            cls.projection_decorators.add(decorator)

    @property
    def sheet_labels(self):
        "The mapping of level method to corresponding label"
        return dict([el for d in self.sheet_decorators
                     for el in d.labels.items()])

    @property
    def sheet_types(self):
        "The mapping of level label to sheet type"
        return dict([el for d in self.sheet_decorators
                     for el in d.types.items()])

    @property
    def projection_labels(self):
        "The mapping of projection method to corresponding label"
        return dict([el for d in self.projection_decorators
                     for el in d.labels.items()])

    @property
    def projection_types(self):
        "The mapping of projection label to projection type"
        return dict([el for d in self.projection_decorators
                     for el in d.types.items()])


    def __init__(self, setup_options=True, register=True, **params):
        if register:
            self._register_global_params(params)
        super(Model,self).__init__(**params)

        self.attrs = AttrTree()
        self.training_patterns = AttrTree()
        self.sheets = AttrTree()
        self.projections = AttrTree()

        self.setup(setup_options)


    def _register_global_params(self, params):
        """
        Register the parameters of this object as global parameters
        available for users to set from the command line.  Values
        supplied as global parameters will override those of the given
        dictionary of params.
        """

        for name,obj in self.params().items():
            global_params.add(**{name:obj})

        for name,val in params.items():
            global_params.params(name).default=val

        params.update(global_params.get_param_values())
        params["name"]=self.name


    #==============================================#
    # Public methods to be implemented by modelers #
    #==============================================#

    def setup_attributes(self, attrs):
        """
        Method to precompute any useful attributes from the class
        parameters. For instance, if there is a ``num_lags``
        parameter, this method could compute the actual projection
        delays and store it as attrs.lags. The return value is the
        updated attrs AttrTree.

        In addition, this method can be used to configure class
        attributes of the model components.
        """
        return attrs


    def setup_training_patterns(self):
        """
        Returns a dictionary of PatternGenerators to be added to
        self.training_patterns, with the target sheet name keys and
        pattern generator values.
        """
        raise NotImplementedError


    def setup_sheets(self):
        """
        Returns a dictionary of properties or equivalent Lancet.Args
        object. Each outer key must be the level name and the values
        are lists of property dictionaries for the sheets at that
        level (or equivalent Lancet Args object). For instance, two
        LGN sheets at the 'LGN' level could be defined by either:

        {'LGN':[{'polarity':'ON'}, {'polarity':'OFF'}]}
        OR
        {'LGN':lancet.List('polarity', ['ON', 'OFF'])}

        The specified properties are used to initialize the sheets
        AttrTree with SheetSpec objects.
        """
        raise NotImplementedError


    def setup_analysis(self):
        """
        Set up appropriate defaults for analysis functions in
        topo.analysis.featureresponses.
        """
        pass


    #====================================================#
    # Remaining methods should not need to be overridden #
    #====================================================#

    def setup(self,setup_options):
        """
        This method can be used to setup certain parts of the
        submodel.  If setup_options=True, all setup methods are
        called.  setup_options can also be a list, whereas all list
        items of available_setup_options are accepted.

        Available setup options are:
        'training_patterns','sheets','projections' and 'analysis'.

        Please consult the docstring of the Model class for more
        information about each setup option.
        """
        available_setup_options = ['attributes',
                                   'training_patterns',
                                   'sheets',
                                   'projections',
                                   'analysis']

        if setup_options==True:
            setup_options = available_setup_options

        if 'attributes' in setup_options:
            self.attrs = self.setup_attributes(self.attrs)

        if 'training_patterns' in setup_options:
            training_patterns = self.setup_training_patterns()
            for name, training_pattern in training_patterns.items():
                self.training_patterns.set_path(name, training_pattern)
        if 'sheets' in setup_options:
            sheet_properties = self.setup_sheets()

            for level, property_list in sheet_properties.items():
                sheet_type = self.sheet_types[level]

                if isinstance(property_list, lancet.Identity):
                    property_list = [{}]
                elif isinstance(property_list, lancet.Args):
                    property_list = property_list.specs
                # If an empty lancet Args() or an empty list
                elif not property_list:
                    continue

                for properties in property_list:
                    spec_properties = dict(level=level, **properties)
                    sheet_spec = SheetSpec(sheet_type, spec_properties)
                    self.sheets.set_path(str(sheet_spec), sheet_spec)

            self._update_sheet_spec_parameters()
        if 'projections' in setup_options:
            self._compute_projection_specs()
        if 'analysis' in setup_options:
            self._setup_analysis()


    def _update_sheet_spec_parameters(self):
        for sheet_spec in self.sheets.path_items.values():
            param_method = self.sheet_labels.get(sheet_spec.level, None)
            if not param_method:
                raise Exception("Parameters for sheet level %r not specified" % sheet_spec.level)

            updated_params = param_method(self,sheet_spec.properties)
            sheet_spec.update_parameters(updated_params)


    def _matchcondition_holds(self, matchconditions, src_sheet):
        """
        Given a dictionary of properties to match and a target sheet
        spec, return True if the matchcondition holds else False.
        """
        matches=True
        if matchconditions is None:
            return False
        for incoming_key, incoming_value in matchconditions.items():
            if incoming_key in src_sheet.properties and \
                    str(src_sheet.properties[incoming_key]) not in str(incoming_value):
                matches=False
                break

        return matches

    def _compute_projection_specs(self):
        """
        Loop through all possible combinations of SheetSpec objects in
        self.sheets If the src_sheet fulfills all criteria specified
        in dest_sheet.matchconditions, create a new ProjectionSpec
        object and add this item to self.projections.
        """
        sheetspec_product = itertools.product(self.sheets.path_items.values(),
                                              self.sheets.path_items.values())
        for src_sheet, dest_sheet in sheetspec_product:

            has_matchcondition = (dest_sheet.level in self.matchconditions)
            conditions = (self.matchconditions.compute_conditions(
                          dest_sheet.level, self,dest_sheet.properties)
                          if has_matchcondition else {})

            for matchname, matchconditions in conditions.items():

                if self._matchcondition_holds(matchconditions, src_sheet):
                    proj = ProjectionSpec(self.projection_types[matchname],
                                          src_sheet, dest_sheet)

                    paramsets = self.projection_labels[matchname](self, src_sheet.properties, 
                                                                  dest_sheet.properties)
                    paramsets = [paramsets] if isinstance(paramsets, dict) else paramsets
                    for paramset in paramsets:
                        proj = ProjectionSpec(self.projection_types[matchname],
                                              src_sheet, dest_sheet)
                        proj.update_parameters(paramset)

                        # HACK: Used by the other hack below for ordering
                        # projections when time_dependent=False
                        proj.matchname = matchname

                        path = (str(dest_sheet), str(src_sheet), paramset['name'])
                        self.projections.set_path(path, proj)


    def _order_projections(self, projections):
        """
        A hack to take the order to initialize sheets into account As
        soon as weight initialization is done with time_dependent=True
        the projection order will no longer be important.
        """
        connection_order=['afferent_projections',
                          'afferent_center_projections',
                          'afferent_surround_projections',
                          'lateral_gain_control_projections',
                          'afferent_ON_projections',
                          'afferent_OFF_projections',
                          'lateral_excitatory_projections',
                          'lateral_inhibitory_projections']

        return sorted(projections,
                      key=lambda p: connection_order.index(p.matchname))


    def __call__(self,instantiate_options=True):
        """
        Instantiates all sheets or projections in self.sheets or
        self.projections and registers them in the topo.sim instance.

        If instantiate_options=True, all items are initialised
        instantiate_options can also be a list, whereas all list items
        of available_instantiate_options are accepted.

        Available instantiation options are: 'sheets' and
        'projections'.

        Please consult the docstring of the Model class for more
        information about each instantiation option.
        """

        available_instantiate_options = ['sheets','projections']
        if instantiate_options==True:
            instantiate_options=available_instantiate_options

        if 'sheets' in instantiate_options:
            for sheet_spec in self.sheets.path_items.itervalues():
                self.message('Level ' + sheet_spec.level + ': Sheet ' + str(sheet_spec))
                sheet_spec()

        if 'projections' in instantiate_options:
            # No need to call _ordered_projections if time-dependent
            projections = self.projections.path_items.itervalues()
            for proj in self._order_projections(projections):
                self.message('Match: ' + proj.matchname + ': Connection ' + str(proj.src) + \
                             '->' + str(proj.dest) + ' ' + proj.parameters['name'])
                proj()


# Register the sheets and projections available in Topographica
from topo.sheet import optimized as sheetopt
from topo.projection import optimized as projopt
from topo import projection

sheet_classes = [c for c in topo.sheet.__dict__.values() if
                 (isinstance(c, type) and issubclass(c, topo.sheet.Sheet))]

sheet_classes_opt = [c for c in sheetopt.__dict__.values() if
                     (isinstance(c, type) and issubclass(c, topo.sheet.Sheet))]

projection_classes = [c for c in projection.__dict__.values() if
                      (isinstance(c, type) and issubclass(c, projection.Projection))]

projection_classes_opt = [c for c in projopt.__dict__.values() if
                          (isinstance(c, type) and issubclass(c, topo.sheet.Sheet))]

for obj_class in (sheet_classes + sheet_classes_opt
                  + projection_classes + projection_classes_opt):

    with stdout_redirected(open(os.devnull, 'w')):
        Model.register_decorator(obj_class)