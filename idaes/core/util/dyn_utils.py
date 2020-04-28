# -*- coding: UTF-8 -*-
##############################################################################
# Institute for the Design of Advanced Energy Systems Process Systems
# Engineering Framework (IDAES PSE Framework) Copyright (c) 2018-2019, by the
# software owners: The Regents of the University of California, through
# Lawrence Berkeley National Laboratory,  National Technology & Engineering
# Solutions of Sandia, LLC, Carnegie Mellon University, West Virginia
# University Research Corporation, et al. All rights reserved.
#
# Please see the files COPYRIGHT.txt and LICENSE.txt for full copyright and
# license information, respectively. Both files are also available online
# at the URL "https://github.com/IDAES/idaes-pse".
##############################################################################
"""
This module contains utility functions for dynamic IDAES models.
"""

from pyomo.environ import Block, Constraint, Var
from pyomo.dae import ContinuousSet, DerivativeVar
from pyomo.kernel import ComponentSet

from idaes.core import FlowsheetBlock
from collections import Counter
import idaes.logger as idaeslog

__author__ = "Robert Parker"


def is_explicitly_indexed_by(comp, *sets):
    """
    Function for determining whether a pyomo component is indexed by a 
    set or group of sets.

    Args:
        comp : some Pyomo component, possibly indexed
        sets : Pyomo Sets to check indexing by

    Returns:
        A bool that is True if comp is directly indexed each set in sets.
    """
    if not comp.is_indexed():
        return False
    n_sets = len(sets)
    if n_sets == 0:
        raise ValueError('Must provide at least one set')
    s_dim = [s.dimen for s in sets]
    total_s_dim = sum(s_dim)
    c_dim = comp.dim()
    if c_dim < total_s_dim:
        # Cannot be indexed as such if dimension is too low
        return False
    elif n_sets == 1 and s_dim[0] == c_dim:
        # This is the only way the index_set is one of the sets provided.
        # Otherwise index_set is a _SetProduct
        return comp.index_set() is sets[0]
    elif c_dim >= total_s_dim:
        # Assume the only way to be indexed by all sets is if
        # we're indexed by a _SetProduct containing all sets
        if not hasattr(comp.index_set(), 'set_tuple'):
            return False
        # Convert set_tuple to a ComponentSet so a different
        # pyomo:Set with the same elements will not be conflated.

        if hasattr(comp.index_set(), 'subsets'):
            set_set = ComponentSet(comp.index_set().subsets())
        else:
            set_set = ComponentSet(comp.index_set().set_tuple)
        return all([s in set_set for s in sets])


def is_implicitly_indexed_by(comp, s, stop_at=None):
    """
    Function for determining whether a component is contained in a 
    block that is indexed by a particular set.

    Args: 
        comp : Component whose parent blocks are checked
        s : Set for which indices are checked
        stop_at : Block at which to stop searching if reached, regardless
                  of whether or not it is indexed by s
                  
    Returns:
        Bool that is true if comp is contained in a block indexed by s
    """
    parent = comp.parent_block()

    # Stop when top-level block has been reached
    while parent is not None:
        # If we have reached our stopping point, quit.
        if parent is stop_at:
            return False

        # Look at the potentially-indexed block containing our component
        parent = parent.parent_component()
        # Check again for the stopping point in case an IndexedBlock was used
        if parent is stop_at:
            return False

        # Check potentially-indexed block for index s:
        if is_explicitly_indexed_by(parent, s):
            return True
        # Continue up the tree, checking the parent block of our
        # potentially-indexed block:
        else:
            parent = parent.parent_block()
    # Return False if top-level block was reached
    return False


def get_index_set_except(comp, *sets):
    """ 
    Function for getting indices of a component over a product of its
    indexing sets other than those specified. Indices for the specified 
    sets can be used to construct indices of the proper dimension for the 
    original component via the index_getter function.

    Args:
        comp : Component whose indexing sets are to be manipulated
        sets : Sets to omit from the set_except product

    Returns:
        A dictionary. Maps 'set_except' to a Pyomo Set or SetProduct
        of comp's index set, excluding those in sets. Maps
        'index_getter' to a function that returns an index of the
        proper dimension for comp, given an element of set_except
        and a value for each set excluded. These values must be provided
        in the same order their Sets were provided in the sets argument.
    """
    n_set = len(sets)
    s_set = ComponentSet(sets)
    total_s_dim = sum([s.dimen for s in sets])
    info = {}

    if not is_explicitly_indexed_by(comp, *sets):
        msg = (comp.name + ' is not indexed by at least one of ' +
                str([s.name for s in sets]))
        raise ValueError(msg)

    index_set = comp.index_set()
    if hasattr(index_set, 'set_tuple'):
        if hasattr(index_set, 'subsets'):
            set_tuple = list(index_set.subsets())
        else:
            set_tuple = index_set.set_tuple
        counter = Counter([id(_) for _ in set_tuple])
        for s in sets:
            if counter[id(s)] != 1:
                msg = 'Cannot omit sets that appear multiple times'
                raise ValueError(msg)
        # Need to know the location of each set within comp's index_set
        # location will map:
        #     location_in_comp_index_set -> location_in_sets
        location = {}
        other_ind_sets = []
        for ind_loc, ind_set in enumerate(set_tuple):
            found_set = False
            for s_loc, s_set in enumerate(sets):
                if ind_set is s_set:
                    location[ind_loc] = s_loc
                    found_set = True
                    break
            if not found_set:
                other_ind_sets.append(ind_set)
    else:
        # If index_set has not set_tuple, it must be a SimpleSet, and 
        # len(sets) == 1. Location in sets and in comp's indexing set
        # are the same.
        location = {0: 0}
        other_ind_sets = []

    if comp.dim() == total_s_dim: 
        # comp indexed by all sets and having this dimension
        # is sufficient to know that comp is only indexed by 
        # Sets in *sets

        # In this case, return the trivial set_except and index_getter

        # Problem: cannot construct location without a set tuple
        #          is that a problem with this syntax?
        #          Here len(newvals) should == 1
        info['set_except'] = [None]
        # index_getter returns an index corresponding to the values passed to
        # it, re-ordered according to order of indexing sets in component.
        info['index_getter'] = (lambda incomplete_index, *newvals:
                newvals[0] if len(newvals) <= 1 else
                tuple([newvals[location[i]] for i in location]))
        return info

    # Now may assume other_ind_sets is nonempty.
    if len(other_ind_sets) == 1:
        set_except = other_ind_sets[0]
    elif len(other_ind_sets) >= 2:
        set_except = other_ind_sets[0].cross(*other_ind_sets[1:])
    else:
        raise ValueError('Did not expect this to happen')

    index_getter = (lambda incomplete_index, *newvals:
            _complete_index(location, incomplete_index, *newvals))

    info['set_except'] = set_except
    info['index_getter'] = index_getter
    return info


def _complete_index(loc, index, *newvals):
    """
    Function for inserting new values into a partial index.
    Used by get_index_set_except function to construct the 
    index_getter function for completing indices of a particular
    component with particular sets excluded.

    Args:
        loc : Dictionary mapping location in the new index to
              location in newvals
        index : Partial index
        newvals : New values to insert into index. Can be scalars
                  or tuples (for higher-dimension sets)

    Returns:
        An index (tuple) with values from newvals inserted in 
        locations specified by loc
    """
    if type(index) is not tuple:
        index = (index,)
    keys = sorted(loc.keys())
    if len(keys) != len(newvals):
        raise ValueError('Wrong number of values to complete index')
    for i in sorted(loc.keys()):
        newval = newvals[loc[i]]
        if type(newval) is not tuple:
            newval = (newval,)
        index = index[0:i] + newval + index[i:]
    return index


def get_activity_dict(b):
    """
    Function that builds a dictionary telling whether or not each
    ConstraintData and BlockData object in a model is active.
    Uses the objects' ids as the hash.

    Args:
        b : A Pyomo Block to be searched for active components

    Returns:
        A dictionary mapping id of constraint and block data objects
        to a bool indicating if they are active
    """
    # Note: active constraints/blocks contained in an inactive block will still
    # be marked as active.
    return {id(con): con.active 
                     for con in b.component_data_objects((Constraint, Block))}


def get_fixed_dict(b):
    """
    Function that builds a dictionary telling whether or not each VarData
    object in a model is fixed. Uses the objects' ids as the hash.

    Args:
        b : A Pyomo block to be searched for fixed variables

    Returns:
        A dictionary mapping id of VarData objects to a bool indicating if
        they are fixed
    """
    return {id(var): var.fixed for var in b.component_data_objects(Var)}


def deactivate_model_at(b, cset, pts, outlvl=idaeslog.NOTSET):
    """
    Finds any block or constraint in block b, indexed explicitly (and not 
    implicitly) by cset, and deactivates it at points specified. 
    Implicitly indexed components are excluded because one of their parent 
    blocks will be deactivated, so deactivating them too would be redundant.

    Args:
        b : Block to search
        cset : ContinuousSet of interest
        pts : Value or list of values, in ContinuousSet, to deactivate at

    Returns:
        A dictionary mapping points in pts to lists of
        component data that have been deactivated there
    """
    if not type(pts) is list:
        pts = [pts]
    for pt in pts:
        if not pt in cset:
            msg = str(pt) + ' is not in ContinuousSet ' + cset.name
            raise ValueError(msg)
    deactivated = {pt: [] for pt in pts}
    
    visited = set()
    for comp in b.component_objects([Block, Constraint], active=True):
        # Record components that have been visited in case component_objects
        # contains duplicates (due to references)
        if id(comp) in visited:
            continue
        visited.add(id(comp))

        if (is_explicitly_indexed_by(comp, cset) and
                not is_implicitly_indexed_by(comp, cset)):
            info = get_index_set_except(comp, cset)
            non_cset_set = info['set_except']
            index_getter = info['index_getter']

            for non_cset_index in non_cset_set:
                for pt in pts:
                    index = index_getter(non_cset_index, pt)
                    try:
                        comp[index].deactivate()
                        deactivated[pt].append(comp[index]) 
                    except KeyError:
                        # except KeyError to allow Constraint/Block.Skip
                        msg = (comp.name + ' has no index ' + str(index))
                        init_log = idaeslog.getInitLogger(__name__, outlvl)
                        init_log.warning(msg)
                        continue
                 
    return deactivated


def deactivate_constraints_unindexed_by(b, time):
    """
    Searches block b for and constraints not indexed by time
    and deactivates them. 

    Args:
        b : Block to search
        time : Set with respect to which to find unindexed constraints

    Returns:
        List of constraints deactivated
    """
    conlist = []
    
    visited = set()
    for comp in b.component_objects(Constraint, active=True):
        if id(comp) in visited:
            continue
        visited.add(id(comp))

        if (not is_explicitly_indexed_by(comp, time) and
                not is_implicitly_indexed_by(comp, time)):
            for index in comp:
                compdata = comp[index]
                if compdata.active:
                    compdata.deactivate()
                    conlist.append(compdata)

    return conlist


def fix_vars_unindexed_by(b, time):
    """
    Searches block b for variables not indexed by time
    and fixes them. 

    Args:
        b : Block to search
        time : Set with respect to which to find unindexed variables

    Returns:
        List of variables fixed
    """
    varlist = []

    visited = set()
    for var in b.component_objects(Var):
        if id(var) in visited:
            continue
        visited.add(id(var))

        if (not is_explicitly_indexed_by(var, time) and
                not is_implicitly_indexed_by(var, time)):
            for index in var:
                vardata = var[index]
                if (not vardata.fixed and vardata.value is not None):
                    # Can't fix a variable with a value of None, but this
                    # should be called after a solve, so any variable with
                    # value of None is stale and won't be sent to solver,
                    # so it doesn't need to be fixed to maintain correct
                    # degrees of freedom
                    vardata.fix()
                    varlist.append(vardata)

    return varlist


def get_location_of_coordinate_set(setprod, subset):
    """For a SetProduct and some 1-dimensional coordinate set of that
    SetProduct, returns the location of an index of the coordinate
    set within the index of the setproduct.

    Args:
        setprod : SetProduct containing the subset of interest
        subset : 1-dimensional set whose location will be found in the
                 SetProduct
    
    Returns:
        Integer location of the subset within the SetProduct
    """
    if subset.dimen != 1:
        # This could be supported in the future if there is demand for it
        raise ValueError(
            'Cannot get the location of %s because it is multi-dimensional'
            %(subset.name))

    loc = None
    i = 0
    found = False
    if hasattr(setprod, 'subsets'):
        subsets = setprod.subsets()
    elif hasattr(setprod, 'set_tuple'):
        subsets = setprod.set_tuple
    else:
        subsets = [setprod]
    for _set in subsets:
        if _set is subset:
            if found:
                raise ValueError(
                    'Cannot get the location of %s because it appears '
                    'multiple times'%(_set.name))
            found = True
            loc = i
            i += 1
        else:
            i += _set.dimen
    return loc


def get_index_of_set(comp, wrt):
    """For some data object of an indexed component, gets the value of the
    index corresponding to some 1-dimensional pyomo set.

    Args: 
        comp : Component data object whose index will be searched
        wrt : Set whose index will be searched for

    Returns:
        Value of the specified set in the component data object
    """
    parent = comp.parent_component()
    if not is_explicitly_indexed_by(parent, wrt):
        raise ValueError(
                "Component %s is not explicitly indexed by set %s."
                %(comp.name, wrt.name))

    index = comp.index() 
    if not type(index) is tuple:
        index = (index,)
    loc = get_location_of_coordinate_set(parent.index_set(), wrt)
    return index[loc]


def get_implicit_index_of_set(comp, wrt):
    """For some data object contained (at some level of the hierarchy) in a
    block indexed by wrt, returns the index corresponding to wrt in that
    block.

    Args:
        comp : Component data object whose (parent blocks') indices will be
               searched
        wrt : Set whose index will be searched for

    Returns:
        Value of the specified set 
    """
    val = None
    found = False
    if is_explicitly_indexed_by(comp.parent_component(), wrt):
        val = get_index_of_set(comp, wrt)
        found = True

    parent_block = comp.parent_block()
    while parent_block is not None:
        parent_component = parent_block.parent_component()
        if is_explicitly_indexed_by(parent_component, wrt):
            if found:
                raise ValueError(
                        "Cannot get the index of set %s because it appears "
                        "multiple times in the hierarchy"%(wrt.name))
            val = get_index_of_set(parent_block, wrt)
            found = True
        parent_block = parent_block.parent_block()

    # Will return val even if it is None. 
    # User can decide what to do in this case.
    return val


def get_derivatives_at(b, time, pts):
    """
    Finds derivatives with respect to time at points specified.
    No distinction made for multiple derivatives or mixed partials.

    Args:
        b : Block to search for derivatives
        time : ContinuousSet to look for derivatives with respect to
        pts : Value or list of values in time set at which to return 
              derivatives

    Returns
        Dictionary mapping time points to lists of derivatives
        at those points
    """
    if not type(pts) is list:
        pts = [pts]
    dvdict = {pt: [] for pt in pts}

    visited = set()
    for var in b.component_objects(Var):
        if id(var) in visited:
            continue
        visited.add(id(var))

        if not isinstance(var, DerivativeVar):
            continue
        if time not in ComponentSet(var.get_continuousset_list()):
            continue

        info = get_index_set_except(var, time)
        non_time_set = info['set_except']
        index_getter = info['index_getter']
        for pt in pts:
            for non_time_index in non_time_set:
                index = index_getter(non_time_index, pt)
                dvdict[pt].append(var[index])

    return dvdict


# TODO: should be able to replace this function everywhere
#       with getname and find_component
#       ^ not true. Cannot call find_component from a BlockData object
#                   Or on a name containing a decimal index
#       component looks like a similar substitute for BlockDatas, but
#       cannot seem to call on names including indices at all
def path_from_block(comp, blk, include_comp=False):
    """
    Returns a list of tuples with (local_name, index) pairs required
    to locate comp from blk

    Args:
        comp : Component(Data) object to locate
        blk : Block(Data) to locate comp from
        include_comp : Bool of whether or not to include the
                       local_name, index of the component itself

    Returns:
        A list of string, index tuples that can be used to locate
        comp from blk
    """
    parent_data = comp.parent_block()
    route = []
    # Walk up the hierarchy tree until blk is reached
    while parent_data != blk:
        parent_obj = parent_data.parent_component()
        # Record the local name and index required to locate the current block
        # from the parent_component of its parent_block
        # Pre-pend to the existing (name, index) list
        route = [(parent_obj.local_name, parent_data.index())] + route
        # If top-levelmodel has been reached, break
        # (This should not happen)
        if parent_data.parent_block() == None:
            break
        parent_data = parent_data.parent_block()

    # Append comp's name and index to the list if desired:
    if include_comp:
        if hasattr(comp, 'index'):
            route.append((comp.parent_component().local_name, comp.index()))
        else:
            # Not obvious what the right thing to do is if comp has no index
            # attribute... 
            route.append((comp.parent_component().local_name, None))
    return route


def find_comp_in_block(tgt_block, src_block, src_comp, allow_miss=False):
    """This function finds a component in a source block, then uses the same
    local names and indices to try to find a corresponding component in a target
    block. This is used when we would like to verify that a component of the 
    same name exists in the target block, as in model predictive control where
    certain variables must be correllated between plant and controller model.

    Args:
        tgt_block : Target block that will be searched for component
        src_block : Source block in which the original component is located
        src_comp : Component whose name will be searched for in target block
        allow_miss : If True, will ignore attribute and key errors due to 
                     searching for non-existant components in the target model

    Returns:
        Component with the same name in the target block
    """

    local_parent = tgt_block
    for r in path_from_block(src_comp, src_block, include_comp=False):
        # Don't include comp as I want to use this to find IndexedComponents,
        # for which [r[1]] will result in a KeyError.
        try:
            local_parent = getattr(local_parent, r[0])[r[1]]
        except (AttributeError, KeyError):
            if allow_miss:
                return None
            else:
                raise

    # This logic should return the IndexedComponent or ComponentData,
    # whichever is appropriate
    try:
        tgt_comp = getattr(local_parent, src_comp.parent_component().local_name)
    except AttributeError:
        if allow_miss:
            return None
        else:
            raise
    # tgt_comp is now an indexed component or simple component

    if hasattr(src_comp, 'index'):
        # If comp has index, attempt to access it in tgt_comp
        index = src_comp.index()
        try:
            tgt_comp = tgt_comp[index]
        except KeyError:
            if allow_miss:
                return None
            else:
                raise

    return tgt_comp


def find_comp_in_block_at_time(tgt_block, src_block, src_comp,
                               time, t0, allow_miss=False):
    """This function finds a component in a source block, then uses the same
    local names and indices to try to find a corresponding component in a target
    block, with the exception of time index in the target component, which is
    replaced by a specified time point. This is used for validation of a
    component by its name in the case where blocks may differ by at most time
    indices, for example validating a steady-state model or a model with a
    different time discretization.

    Args:
        tgt_block : Target block that will be searched for component
        src_block : Source block in which the original component is located
        src_comp : Component whose name will be searched for in target block
        time : Set whose index will be replaced in the target component
        t0 : Index of the time set that will be used in the target
             component
        allow_miss : If True, will ignore attribute and key errors due to 
                     searching for non-existant components in the target model

    """
    # Could extend this to allow replacing indices of multiple sets
    # (useful for PDEs)

    if t0 not in time:
        raise KeyError(
            't0 must be in the time set')
    if time.model() is not tgt_block.model():
        raise ValueError(
            'time must belong to the same model as the target block')
    if src_block.model() is not src_comp.model():
        raise ValueError(
            'src_block and src_comp must be components of the same model')

    local_parent = tgt_block
    for r in path_from_block(src_comp, src_block, include_comp=False):
        # Don't include comp as I want to use this to find IndexedComponents,
        # for which [r[1]] will result in a KeyError.

        # If local_parent is indexed by time, need to replace time index
        # in r[1]

        try:
            local_parent = getattr(local_parent, r[0])
        except AttributeError:
            if allow_miss:
                return None
            else:
                raise

        index = r[1]

        # Can abstract the following into a function:
        # replace_time_index or something
        if is_explicitly_indexed_by(local_parent, time):
            index_set = local_parent.index_set()
            time_loc = get_location_of_coordinate_set(index_set, time)

            if type(index) is not tuple:
                index = (index,)
            index = list(index)

            # Replace time index with t0
            index[time_loc] = t0
            index = tuple(index)
            
        try:
            local_parent = local_parent[index]
        except KeyError:
            if allow_miss:
                return None
            else:
                raise

    # This logic should return the IndexedComponent or ComponentData,
    # whichever is appropriate
    try:
        tgt_comp = getattr(local_parent, src_comp.parent_component().local_name)
    except AttributeError:
        if allow_miss:
            return None
        else:
            raise
    # tgt_comp is now an indexed component or simple component

    if hasattr(src_comp, 'index'):
        # If comp has index, attempt to access it in tgt_comp
        index = src_comp.index()

        if is_explicitly_indexed_by(tgt_comp, time):
            index_set = tgt_comp.index_set()
            time_loc = get_location_of_coordinate_set(index_set, time)

            if type(index) is not tuple:
                index = (index,)
            index = list(index)

            # Replace time index with t0
            index[time_loc] = t0
            index = tuple(index)

        try:
            tgt_comp = tgt_comp[index]
        except KeyError:
            if allow_miss:
                return None
            else:
                raise

    return tgt_comp


def copy_non_time_indexed_values(fs_tgt, fs_src, copy_fixed=True):
    """
    Function to set the values of all variables that are not (implicitly
    or explicitly) indexed by time to their values in a different flowsheet.

    Args:
        fs_tgt : Flowsheet into which values will be copied.
        fs_src : Flowsheet from which values will be copied.
        copy_fixed : Bool marking whether or not to copy over fixed variables
                     in the target flowsheet.

    Returns:
        None
    """
    time_tgt = fs_tgt.time

    var_visited = set()
    for var_tgt in fs_tgt.component_objects(Var,
                                            descend_into=False):
        if id(var_tgt) in var_visited:
            continue
        var_visited.add(id(var_tgt))

        if is_explicitly_indexed_by(var_tgt, time_tgt):
            continue
        var_src = fs_src.find_component(var_tgt.local_name)
        # ^ this find_component is fine because var_tgt is a Var not VarData
        # and its local_name is used. Assumes that there are no other decimal
        # indices in between fs_src and var_src

        if var_src is None:
            # Log a warning
            msg = ('Warning copying values: ' + varname + 
                   ' does not exist in source block ' + fs_src.name)
            init_log = idaeslog.getInitLogger(__name__, outlvl)
            init_log.warning(msg)
            continue

        for index in var_tgt:
            if not copy_fixed and var_tgt[index].fixed:
                continue
            var_tgt[index].set_value(var_src.value)

    blk_visited = set()
    for blk_tgt in fs_tgt.component_objects(Block):

        if id(blk_tgt) in blk_visited:
            continue
        blk_visited.add(id(blk_tgt))

        if (is_implicitly_indexed_by(blk_tgt, time_tgt) or 
                is_explicitly_indexed_by(blk_tgt, time_tgt)):
            continue
        # block is not even implicitly indexed by time
        for b_index in blk_tgt:

            var_visited = set()
            for var_tgt in blk_tgt[b_index].component_objects(Var,
                                                     descend_into=False):
                if id(var_tgt) in var_visited:
                    continue
                var_visited.add(id(var_tgt))

                if is_explicitly_indexed_by(var_tgt, time_tgt):
                    continue
    
                # can't used find_component(local_name) here because I might
                # have decimal indices
                try:
                    local_parent = fs_src
                    for r in path_from_block(var_tgt, fs_tgt):
                        local_parent = getattr(local_parent, r[0])[r[1]]
                except AttributeError:
                    # log warning
                    msg = ('Warning copying values: ' + r[0] + 
                           ' does not exist in source' + local_parent.name)
                    init_log = idaeslog.getInitLogger(__name__, outlvl)
                    init_log.warning(msg)
                    continue
                except KeyError:
                    msg = ('Warning copying values: ' + str(r[1]) + 
                           ' is not a valid index for' + 
                           getattr(local_parent, r[0]).name)
                    init_log = idaeslog.getInitLogger(__name__, outlvl)
                    init_log.warning(msg)
                    continue

                var_src = getattr(local_parent, var_tgt.local_name)
    
                for index in var_tgt:
                    if not copy_fixed and var_tgt[index].fixed:
                        continue
                    var_tgt[index].set_value(var_src[index].value)


def copy_values_at_time(fs_tgt, fs_src, t_target, t_source, 
        copy_fixed=True, outlvl=idaeslog.NOTSET):
    """
    Function to set the values of all (explicitly or implicitly) time-indexed 
    variables in a flowsheet to similar values (with the same name) but at 
    different points in time and (potentially) in different flowsheets.

    Args:
        fs_tgt : Target flowsheet, whose variables' values will get set
        fs_src : Source flowsheet, whose variables' values will be used to 
                 set those of the target flowsheet. Could be the target
                 flowsheet
        t_target : Target time point
        t_source : Source time point
        copy_fixed : Bool of whether or not to copy over fixed variables in 
                     target model 
        outlvl : IDAES logger output level

    Returns:
        None
    """
    time_target = fs_tgt.time
    var_visited = set()
    for var_target in fs_tgt.component_objects(Var):
        if id(var_target) in var_visited:
            continue
        var_visited.add(id(var_target))

        if not is_explicitly_indexed_by(var_target, time_target):
            continue
        n = var_target.index_set().dimen

        local_parent = fs_src

        varname = var_target.getname(fully_qualified=True, relative_to=fs_tgt)
        # Calling find_component here makes the assumption that varname does not 
        # contain decimal indices.
        var_source = fs_src.find_component(varname)
        if var_source is None:
            # Log a warning
            msg = ('Warning copying values: ' + varname + 
                   ' does not exist in source block ' + fs_src.name)
            init_log = idaeslog.getInitLogger(__name__, outlvl)
            init_log.warning(msg)
            continue
        	
        if n == 1:
            if not copy_fixed and var_target[t_target].fixed:
                continue
            var_target[t_target].set_value(var_source[t_source].value)
        elif n >= 2:
            index_info = get_index_set_except(var_target, time_target)
            non_time_index_set = index_info['set_except']
            index_getter = index_info['index_getter']
            for non_time_index in non_time_index_set:
                source_index = index_getter(non_time_index, t_source)
                target_index = index_getter(non_time_index, t_target)
                if not copy_fixed and var_target[target_index].fixed:
                    continue
                var_target[target_index].set_value(
                        var_source[source_index].value)

    blk_visited = set()
    for blk_target in fs_tgt.component_objects(Block):
        if id(blk_target) in blk_visited:
            continue
        blk_visited.add(id(blk_target))

        if not is_explicitly_indexed_by(blk_target, time_target):
            continue
        n = blk_target.index_set().dimen

        blkname = blk_target.getname(fully_qualified=True, relative_to=fs_tgt)
        blk_source = fs_src.find_component(blkname)
        if blk_source is None:
            # log warning
            msg = ('Warning copying values: ' + blkname + 
                   ' does not exist in source' + fs_src.name)
            init_log = idaeslog.getInitLogger(__name__, outlvl)
            init_log.warning(msg)
            continue

        if n == 1:
            target_index = t_target
            source_index = t_source

            var_visited = set()
            for var_target in blk_target[target_index].component_data_objects(Var):
                if id(var_target) in var_visited:
                    continue
                var_visited.add(id(var_target))

                if not copy_fixed and var_target.fixed:
                    continue

                # Here, find_component will not work from BlockData object
                local_parent = blk_source[source_index]
                for r in path_from_block(var_target, blk_target[target_index]):
                    local_parent = getattr(local_parent, r[0])[r[1]]
                var_source = getattr(local_parent,
                        var_target.parent_component().local_name)[
                                var_target.index()]
                var_target.set_value(var_source.value)

        elif n >= 2:
            index_info = get_index_set_except(blk_target, time_target)
            non_time_index_set = index_info['set_except']
            index_getter = index_info['index_getter']
            for non_time_index in non_time_index_set:
                source_index = index_getter(non_time_index, t_source)
                target_index = index_getter(non_time_index, t_target)

                var_visited = set()
                for var_target in blk_target[target_index].component_data_objects(Var):
                    if id(var_target) in var_visited:
                        continue
                    var_visited.add(id(var_target))

                    if not copy_fixed and var_target.fixed:
                        continue

                    local_parent = blk_source[source_index]
                    for r in path_from_block(var_target,
                                             blk_target[target_index]):
                        local_parent = getattr(local_parent, r[0])[r[1]]
                    var_source = getattr(local_parent,
                            var_target.parent_component().local_name)[
                                    var_target.index()]
                    var_target.set_value(var_source.value)

