# Copyright 2021 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Classes that specify how boundary conditions are applied to arrays."""

import dataclasses
from typing import Sequence, Tuple, Optional
from jax import lax
import jax.numpy as jnp
from jax_cfd.base import grids
import numpy as np

BoundaryConditions = grids.BoundaryConditions
GridArray = grids.GridArray
GridVariable = grids.GridVariable
GridVariableVector = grids.GridVariableVector


class BCType:
  PERIODIC = 'periodic'
  DIRICHLET = 'dirichlet'
  NEUMANN = 'neumann'


@dataclasses.dataclass(init=False, frozen=True)
class ConstantBoundaryConditions(BoundaryConditions):
  """Boundary conditions for a PDE variable that are constant in space and time.

  Example usage:
    grid = Grid((10, 10))
    array = GridArray(np.zeros((10, 10)), offset=(0.5, 0.5), grid)
    bc = ConstantBoundaryConditions(((BCType.PERIODIC, BCType.PERIODIC),
                                        (BCType.DIRICHLET, BCType.DIRICHLET)),
                                        ((0.0, 10.0),(1.0, 0.0)))
    u = GridVariable(array, bc)

  Attributes:
    types: `types[i]` is a tuple specifying the lower and upper BC types for
      dimension `i`.
    _constant_values: '_constant_values[i]' is a tuple of floats specifying the
      lower and upper BC values for dimension `i`.
  """
  types: Tuple[Tuple[str, str], ...]
  _constant_values: Tuple[Tuple[float, float], ...]

  def __init__(self, types: Sequence[Tuple[str, str]],
               values: Sequence[Tuple[float, float]]):
    types = tuple(types)
    values = tuple(values)
    object.__setattr__(self, 'types', types)
    object.__setattr__(self, '_constant_values', values)

  def shift(
      self,
      u: GridArray,
      offset: int,
      axis: int,
  ) -> GridArray:
    """Shift an GridArray by `offset`.

    Args:
      u: an `GridArray` object.
      offset: positive or negative integer offset to shift.
      axis: axis to shift along.

    Returns:
      A copy of `u`, shifted by `offset`. The returned `GridArray` has offset
      `u.offset + offset`.
    """
    padded = self._pad(u, offset, axis)
    trimmed = self._trim(padded, -offset, axis)
    return trimmed

  def _pad(
      self,
      u: GridArray,
      width: int,
      axis: int,
  ) -> GridArray:
    """Pad a GridArray by `padding`.

    Important: Padding makes no sense past 1 ghost cell. This is sufficient for
    jax_cfd finite difference code.

    Args:
      u: a `GridArray` object.
      width: number of elements to pad along axis. Use negative value for lower
        boundary or positive value for upper boundary.
      axis: axis to pad along.

    Returns:
      Padded array, elongated along the indicated axis.
    """
    if width < 0:  # pad lower boundary
      bc_type = self.types[axis][0]
      padding = (-width, 0)
    else:  # pad upper boundary
      bc_type = self.types[axis][1]
      padding = (0, width)

    full_padding = [(0, 0)] * u.grid.ndim
    full_padding[axis] = padding

    offset = list(u.offset)
    offset[axis] -= padding[0]

    if bc_type == BCType.PERIODIC:
      # self.values are ignored here
      pad_kwargs = dict(mode='wrap')
    elif bc_type == BCType.DIRICHLET:
      if np.isclose(u.offset[axis] % 1, 0.5):  # cell center
        # make the linearly interpolated value equal to the boundary by setting
        # the padded values to the negative symmetric values
        data = (2 * jnp.pad(
            u.data,
            full_padding,
            mode='constant',
            constant_values=self._constant_values) -
                jnp.pad(u.data, full_padding, mode='symmetric'))
        return GridArray(data, tuple(offset), u.grid)
      elif np.isclose(u.offset[axis] % 1, 0):  # cell edge
        pad_kwargs = dict(
            mode='constant', constant_values=self._constant_values)
      else:
        raise ValueError('expected offset to be an edge or cell center, got '
                         f'offset[axis]={u.offset[axis]}')
    elif bc_type == BCType.NEUMANN:
      if (np.isclose(u.offset[axis] % 1, 0) or
          np.isclose(u.offset[axis] % 1, 0.5)):  # cell edge or center
        # in case of cell edge, it forces one-sided first difference to be
        # satisfied. Important: Padding makes no sense past 1 ghost cell.
        # Note: In case of cell center, it computes backward difference.
        # In case of a cell edge, it is neither backward nor forward difference
        # as defined in jax_cfd. It satisfies
        # (u_last_interior - u_boundary)/h = bc. u_ghost are not defined in this
        # case.
        data = (
            jnp.pad(u.data, full_padding, mode='edge') + u.grid.step[axis] *
            (jnp.pad(u.data, full_padding, mode='constant') - jnp.pad(
                u.data,
                full_padding,
                mode='constant',
                constant_values=self._constant_values)))
        return GridArray(data, tuple(offset), u.grid)
      else:
        raise ValueError('expected offset to be an edge or cell center, got '
                         f'offset[axis]={u.offset[axis]}')
    else:
      raise ValueError('invalid boundary type')

    data = jnp.pad(u.data, full_padding, **pad_kwargs)
    return GridArray(data, tuple(offset), u.grid)

  def _trim(
      self,
      u: GridArray,
      width: int,
      axis: int,
  ) -> GridArray:
    """Trim padding from a GridArray.

    Args:
      u: a `GridArray` object.
      width: number of elements to trim along axis. Use negative value for lower
        boundary or positive value for upper boundary.
      axis: axis to trim along.

    Returns:
      Trimmed array, shrunk along the indicated axis.
    """
    if width < 0:  # trim lower boundary
      padding = (-width, 0)
    else:  # trim upper boundary
      padding = (0, width)

    limit_index = u.data.shape[axis] - padding[1]
    data = lax.slice_in_dim(u.data, padding[0], limit_index, axis=axis)
    offset = list(u.offset)
    offset[axis] += padding[0]
    return GridArray(data, tuple(offset), u.grid)

  trim = _trim
  pad = _pad


class HomogeneousBoundaryConditions(ConstantBoundaryConditions):
  """Boundary conditions for a PDE variable.

  Example usage:
    grid = Grid((10, 10))
    array = GridArray(np.zeros((10, 10)), offset=(0.5, 0.5), grid)
    bc = ConstantBoundaryConditions(((BCType.PERIODIC, BCType.PERIODIC),
                                        (BCType.DIRICHLET, BCType.DIRICHLET)))
    u = GridVariable(array, bc)

  Attributes:
    types: `types[i]` is a tuple specifying the lower and upper BC types for
      dimension `i`.
  """

  def __init__(self, types: Sequence[Tuple[str, str]]):

    ndim = len(types)
    values = ((0.0, 0.0),) * ndim
    super(HomogeneousBoundaryConditions, self).__init__(types, values)


# Convenience utilities to ease updating of BoundaryConditions implementation
def periodic_boundary_conditions(ndim: int) -> BoundaryConditions:
  """Returns periodic BCs for a variable with `ndim` spatial dimension."""
  return HomogeneousBoundaryConditions(
      ((BCType.PERIODIC, BCType.PERIODIC),) * ndim)


def dirichlet_boundary_conditions(ndim: int) -> BoundaryConditions:
  """Returns Dirichelt BCs for a variable with `ndim` spatial dimension."""
  return HomogeneousBoundaryConditions(
      ((BCType.DIRICHLET, BCType.DIRICHLET),) * ndim)


def neumann_boundary_conditions(ndim: int) -> BoundaryConditions:
  """Returns Neumann BCs for a variable with `ndim` spatial dimension."""
  return HomogeneousBoundaryConditions(
      ((BCType.NEUMANN, BCType.NEUMANN),) * ndim)


def periodic_and_dirichlet_boundary_conditions(
    bc_vals=None) -> BoundaryConditions:
  """Returns BCs periodic for dimension 0 and Dirichlet for dimension 1."""
  if not bc_vals:
    return HomogeneousBoundaryConditions(((BCType.PERIODIC, BCType.PERIODIC),
                                          (BCType.DIRICHLET, BCType.DIRICHLET)))
  else:
    return ConstantBoundaryConditions(((BCType.PERIODIC, BCType.PERIODIC),
                                       (BCType.DIRICHLET, BCType.DIRICHLET)),
                                      ((0.0, 0.0), bc_vals))


def periodic_and_neumann_boundary_conditions(
    bc_vals=None) -> BoundaryConditions:
  """Returns BCs periodic for dimension 0 and Neumann for dimension 1."""
  if not bc_vals:
    return HomogeneousBoundaryConditions(
        ((BCType.PERIODIC, BCType.PERIODIC), (BCType.NEUMANN, BCType.NEUMANN)))
  else:
    return ConstantBoundaryConditions(
        ((BCType.PERIODIC, BCType.PERIODIC), (BCType.NEUMANN, BCType.NEUMANN)),
        ((0.0, 0.0), bc_vals))


def has_all_periodic_boundary_conditions(*arrays: GridVariable) -> bool:
  """Returns True if arrays have periodic BC in every dimension, else False."""
  for array in arrays:
    for lower_bc_type, upper_bc_type in array.bc.types:
      if lower_bc_type != BCType.PERIODIC or upper_bc_type != BCType.PERIODIC:
        return False
  return True


def get_pressure_bc_from_velocity(v: GridVariableVector) -> BoundaryConditions:
  """Returns pressure boundary conditions for the specified velocity."""
  # Expect each component of v to have the same BC, either both PERIODIC or
  # both DIRICHLET.
  velocity_bc_types = grids.consistent_boundary_conditions(*v).types
  pressure_bc_types = []
  for velocity_bc_lower, velocity_bc_upper in velocity_bc_types:
    if velocity_bc_lower == BCType.PERIODIC:
      pressure_bc_lower = BCType.PERIODIC
    elif velocity_bc_lower == BCType.DIRICHLET:
      pressure_bc_lower = BCType.NEUMANN
    else:
      raise ValueError('Expected periodic or dirichlete velocity BC, '
                       f'got {velocity_bc_lower}')
    if velocity_bc_upper == BCType.PERIODIC:
      pressure_bc_upper = BCType.PERIODIC
    elif velocity_bc_upper == BCType.DIRICHLET:
      pressure_bc_upper = BCType.NEUMANN
    else:
      raise ValueError('Expected periodic or dirichlete velocity BC, '
                       f'got {velocity_bc_upper}')
    pressure_bc_types.append((pressure_bc_lower, pressure_bc_upper))
  return HomogeneousBoundaryConditions(pressure_bc_types)
