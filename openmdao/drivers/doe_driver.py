"""
Design-of-Experiments Driver.
"""
from __future__ import print_function

import sys
import traceback
import inspect

from six import PY3

import numpy as np

from openmdao.core.driver import Driver, RecordingDebugging
from openmdao.core.analysis_error import AnalysisError
from openmdao.utils.record_util import create_local_meta


class DOEGenerator(object):
    """
    Base class for a callable object that generates cases for a DOEDriver.
    """

    def __call__(self, design_vars):
        """
        Generate case.

        Parameters
        ----------
        design_vars : dict
            Dictionary of design variables for which to generate values.

        Yields
        ------
        list
            list of name, value tuples for the design variables.
        """
        pass


class DOEDriver(Driver):
    """
    Design-of-Experiments Driver.

    Options
    -------
    options['run_parallel'] :  bool
        If True and running under MPI, cases will run in parallel. Default is False.

    Attributes
    ----------
    _generator : DOEGenerator
        The case generator
    """

    def __init__(self, generator):
        """
        Constructor.

        Parameters
        ----------
        generator : DOEGenerator
            The case generator
        """
        if not isinstance(generator, DOEGenerator):
            if inspect.isclass(generator):
                raise TypeError("DOEDriver requires an instance of DOEGenerator, "
                                "but a class object was found: %s"
                                % generator.__name__)
            else:
                raise TypeError("DOEDriver requires an instance of DOEGenerator, "
                                "but an instance of %s was found."
                                % type(generator).__name__)

        super(DOEDriver, self).__init__()

        self._generator = generator

        self.options.declare('run_parallel', default=False,
                             desc='Set to True to run the cases in parallel.')

    def _setup_driver(self, problem):
        """
        Prepare the driver for execution.

        This is the final thing to run during setup.

        Parameters
        ----------
        problem : <Problem>
            Pointer to the containing problem.
        """
        super(DOEDriver, self)._setup_driver(problem)

        if self.options['run_parallel']:
            self._comm = self._problem.comm
        else:
            self._comm = None

    def run(self):
        """
        Generate cases and run the model for each set of generated input values.

        Returns
        -------
        boolean
            Failure flag; True if failed to converge, False is successful.
        """
        self.iter_count = 0

        if self._comm:
            case_gen = self._parallel_generator
        else:
            case_gen = self._generator

        for case in case_gen(self._designvars):
            print(self._comm.rank, 'running case:', case)
            metadata = self._prep_case(case, self.iter_count)

            terminate, exc = self._try_case(metadata)

            if exc is not None:
                if PY3:
                    raise exc[0].with_traceback(exc[1], exc[2])
                else:
                    # exec needed here since otherwise python3 will
                    # barf with a syntax error  :(
                    exec('raise exc[0], exc[1], exc[2]')

            self.iter_count += 1

        return False

    def _prep_case(self, case, iter_count):
        """
        Create metadata for the case and set design variables.

        Parameters
        ----------
        case : dict
            Dictionary of input values for the case.
        iter_count : int
            Keep track of iterations for case recording.

        Returns
        -------
        dict
            Information about the running of this case.
        """
        metadata = create_local_meta('DOEDriver')
        metadata['coord'] = (iter_count,)

        for dv_name, dv_val in case:
            self.set_design_var(dv_name, dv_val)

        return metadata

    def _try_case(self, metadata):
        """
        Run case, save exception info and mark the metadata if the case fails.

        Parameters
        ----------
        metadata : dict
            Information about the running of this case.

        Returns
        -------
        bool
            Flag indicating whether or not to terminate execution
        exc_info
            Information about any Exception that occurred in running this case
        """
        terminate = False
        exc = None

        metadata['terminate'] = 0

        with RecordingDebugging(self._get_name(), self.iter_count, self) as rec:
            try:
                failure_flag, _, _ = self._problem.model._solve_nonlinear()
                metadata['success'] = not failure_flag
            except AnalysisError:
                metadata['msg'] = traceback.format_exc()
                metadata['success'] = 0
            except Exception:
                metadata['success'] = 0
                metadata['terminate'] = 1  # tell master to stop sending cases in lb case
                metadata['msg'] = traceback.format_exc()

                print(metadata['msg'])

                # if not self._load_balance:
                exc = sys.exc_info()
                terminate = True

        return terminate, exc

    def _parallel_generator(self, design_vars):
        """
        Generate case for this processor when running under MPI.

        Parameters
        ----------
        design_vars : dict
            Dictionary of design variables for which to generate values.

        Yields
        ------
        list
            list of name, value tuples for the design variables.
        """
        rank = self._comm.rank
        size = self._comm.size

        # exhausted = False

        for i, case in enumerate(self._generator(design_vars)):
            if rank == i % size:
                yield case
            else:
                print(self._comm.rank, 'skipping', i)

        # yield None
