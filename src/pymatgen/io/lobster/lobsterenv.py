"""
This module provides classes to perform analyses of
the local environments (e.g., finding near neighbors)
of single sites in molecules and structures based on
bonding analysis with Lobster.
If you use this module, please cite:
J. George, G. Petretto, A. Naik, M. Esters, A. J. Jackson, R. Nelson, R. Dronskowski, G.-M. Rignanese, G. Hautier,
"Automated Bonding Analysis with Crystal Orbital Hamilton Populations",
ChemPlusChem 2022, e202200123,
DOI: 10.1002/cplu.202200123.
"""

from __future__ import annotations

import collections
import copy
import math
import tempfile
from typing import TYPE_CHECKING, NamedTuple

import numpy as np
from monty.dev import deprecated

from pymatgen.analysis.bond_valence import BVAnalyzer
from pymatgen.analysis.chemenv.coordination_environments.coordination_geometry_finder import LocalGeometryFinder
from pymatgen.analysis.chemenv.coordination_environments.structure_environments import LightStructureEnvironments
from pymatgen.analysis.local_env import NearNeighbors
from pymatgen.electronic_structure.cohp import CompleteCohp
from pymatgen.electronic_structure.core import Spin
from pymatgen.electronic_structure.plotter import CohpPlotter
from pymatgen.io.lobster import Charge, Icohplist
from pymatgen.util.due import Doi, due

if TYPE_CHECKING:
    from typing_extensions import Self

    from pymatgen.core import Structure
    from pymatgen.core.periodic_table import Element

__author__ = "Janine George"
__copyright__ = "Copyright 2021, The Materials Project"
__version__ = "1.0"
__maintainer__ = "J. George"
__email__ = "janinegeorge.ulfen@gmail.com"
__status__ = "Production"
__date__ = "February 2, 2021"

due.cite(
    Doi("10.1002/cplu.202200123"),
    description="Automated Bonding Analysis with Crystal Orbital Hamilton Populations",
)


class LobsterNeighbors(NearNeighbors):
    """
    This class combines capabilities from LocalEnv and ChemEnv to determine coordination environments based on
    bonding analysis.
    """

    def __init__(
        self,
        structure: Structure,
        filename_icohp: str | None = "ICOHPLIST.lobster",
        obj_icohp: Icohplist | None = None,
        are_coops: bool = False,
        are_cobis: bool = False,
        valences: list[float] | None = None,
        limits: tuple[float, float] | None = None,
        additional_condition: int = 0,
        only_bonds_to: list[str] | None = None,
        perc_strength_icohp: float = 0.15,
        noise_cutoff: float = 0.1,
        valences_from_charges: bool = False,
        filename_charge: str | None = None,
        obj_charge: Charge | None = None,
        which_charge: str = "Mulliken",
        adapt_extremum_to_add_cond: bool = False,
        add_additional_data_sg: bool = False,
        filename_blist_sg1: str | None = None,
        filename_blist_sg2: str | None = None,
        id_blist_sg1: str = "ICOOP",
        id_blist_sg2: str = "ICOBI",
    ) -> None:
        """
        Args:
            filename_icohp: (str) Path to ICOHPLIST.lobster or ICOOPLIST.lobster or ICOBILIST.lobster
            obj_icohp: Icohplist object
            structure: (Structure) typically constructed by Structure.from_file("POSCAR")
            are_coops: (bool) if True, the file is a ICOOPLIST.lobster and not a ICOHPLIST.lobster; only tested for
                ICOHPLIST.lobster so far
            are_cobis: (bool) if True, the file is a ICOBILIST.lobster and not a ICOHPLIST.lobster
            valences: (list[float]): gives valence/charge for each element
            limits (tuple[float, float] | None): limit to decide which ICOHPs (ICOOP or ICOBI) should be considered
            additional_condition (int): Additional condition that decides which kind of bonds will be considered
                NO_ADDITIONAL_CONDITION = 0
                ONLY_ANION_CATION_BONDS = 1
                NO_ELEMENT_TO_SAME_ELEMENT_BONDS = 2
                ONLY_ANION_CATION_BONDS_AND_NO_ELEMENT_TO_SAME_ELEMENT_BONDS = 3
                ONLY_ELEMENT_TO_OXYGEN_BONDS = 4
                DO_NOT_CONSIDER_ANION_CATION_BONDS=5
                ONLY_CATION_CATION_BONDS=6
            only_bonds_to: (list[str]) will only consider bonds to certain elements (e.g. ["O"] for oxygen)
            perc_strength_icohp: if no limits are given, this will decide which icohps will still be considered (
            relative to
            the strongest ICOHP (ICOOP or ICOBI)
            noise_cutoff: if provided hardcodes the lower limit of icohps considered
            valences_from_charges: if True and path to CHARGE.lobster is provided, will use Lobster charges (
            Mulliken) instead of valences
            filename_charge: (str) Path to Charge.lobster
            obj_charge: Charge object
            which_charge: (str) "Mulliken" or "Loewdin"
            adapt_extremum_to_add_cond: (bool) will adapt the limits to only focus on the bonds determined by the
            additional condition
            add_additional_data_sg: (bool) will add the information from filename_add_bondinglist_sg1,
            filename_blist_sg1: (str) Path to additional ICOOP, ICOBI data for structure graphs
            filename_blist_sg2: (str) Path to additional ICOOP, ICOBI data for structure graphs
            id_blist_sg1: (str) Identity of data in filename_blist_sg1,
                e.g. "icoop" or "icobi"
            id_blist_sg2: (str) Identity of data in filename_blist_sg2,
                e.g. "icoop" or "icobi".
        """
        if filename_icohp is not None:
            self.ICOHP = Icohplist(are_coops=are_coops, are_cobis=are_cobis, filename=filename_icohp)
        elif obj_icohp is not None:
            self.ICOHP = obj_icohp
        else:
            raise ValueError("Please provide either filename_icohp or obj_icohp")
        self.Icohpcollection = self.ICOHP.icohpcollection
        self.structure = structure
        self.limits = limits
        self.only_bonds_to = only_bonds_to
        self.adapt_extremum_to_add_cond = adapt_extremum_to_add_cond
        self.are_coops = are_coops
        self.are_cobis = are_cobis
        self.add_additional_data_sg = add_additional_data_sg
        self.filename_blist_sg1 = filename_blist_sg1
        self.filename_blist_sg2 = filename_blist_sg2
        self.noise_cutoff = noise_cutoff

        allowed_arguments = ["icoop", "icobi"]
        if id_blist_sg1.lower() not in allowed_arguments or id_blist_sg2.lower() not in allowed_arguments:
            raise ValueError("Algorithm can only work with ICOOPs, ICOBIs")
        self.id_blist_sg1 = id_blist_sg1
        self.id_blist_sg2 = id_blist_sg2
        if add_additional_data_sg:
            if self.id_blist_sg1.lower() == "icoop":
                are_coops_id1 = True
                are_cobis_id1 = False
            elif self.id_blist_sg1.lower() == "icobi":
                are_coops_id1 = False
                are_cobis_id1 = True
            else:
                raise ValueError("only icoops and icobis can be added")
            self.bonding_list_1 = Icohplist(
                filename=self.filename_blist_sg1,
                are_coops=are_coops_id1,
                are_cobis=are_cobis_id1,
            )

            if self.id_blist_sg2.lower() == "icoop":
                are_coops_id2 = True
                are_cobis_id2 = False
            elif self.id_blist_sg2.lower() == "icobi":
                are_coops_id2 = False
                are_cobis_id2 = True
            else:
                raise ValueError("only icoops and icobis can be added")

            self.bonding_list_2 = Icohplist(
                filename=self.filename_blist_sg2,
                are_coops=are_coops_id2,
                are_cobis=are_cobis_id2,
            )

        # will check if the additional condition is correctly delivered
        if additional_condition not in range(7):
            raise ValueError(f"Unexpected {additional_condition=}, must be one of {list(range(7))}")
        self.additional_condition = additional_condition

        # will read in valences, will prefer manual setting of valences
        self.valences: list[float] | None
        if valences is None:
            if valences_from_charges and filename_charge is not None:
                chg = Charge(filename=filename_charge)
                if which_charge == "Mulliken":
                    self.valences = chg.Mulliken
                elif which_charge == "Loewdin":
                    self.valences = chg.Loewdin
            elif valences_from_charges and obj_charge is not None:
                chg = obj_charge
                if which_charge == "Mulliken":
                    self.valences = chg.Mulliken
                elif which_charge == "Loewdin":
                    self.valences = chg.Loewdin
            else:
                bv_analyzer = BVAnalyzer()
                try:
                    self.valences = bv_analyzer.get_valences(structure=self.structure)
                except ValueError:
                    self.valences = None
                    if additional_condition in [1, 3, 5, 6]:
                        raise ValueError(
                            "Valences cannot be assigned, additional_conditions 1, 3, 5 and 6 will not work"
                        )
        else:
            self.valences = valences
        if np.allclose(self.valences or [], np.zeros_like(self.valences)) and additional_condition in [1, 3, 5, 6]:
            raise ValueError("All valences are equal to 0, additional_conditions 1, 3, 5 and 6 will not work")

        if limits is None:
            self.lowerlimit = self.upperlimit = None
        else:
            self.lowerlimit, self.upperlimit = limits

        # will evaluate coordination environments
        self._evaluate_ce(
            lowerlimit=self.lowerlimit,
            upperlimit=self.upperlimit,
            only_bonds_to=only_bonds_to,
            additional_condition=self.additional_condition,
            perc_strength_icohp=perc_strength_icohp,
            adapt_extremum_to_add_cond=adapt_extremum_to_add_cond,
        )

    @property
    def structures_allowed(self) -> bool:
        """Whether this NearNeighbors class can be used with Structure objects?"""
        return True

    @property
    def molecules_allowed(self) -> bool:
        """Whether this NearNeighbors class can be used with Molecule objects?"""
        return False

    @property
    def anion_types(self) -> set[Element]:
        """The types of anions present in crystal structure as a set.

        Returns:
            set[Element]: describing anions in the crystal structure.
        """
        if self.valences is None:
            raise ValueError("No cations and anions defined")

        anion_species = []
        for site, val in zip(self.structure, self.valences):
            if val < 0.0:
                anion_species.append(site.specie)

        return set(anion_species)

    @deprecated(anion_types)
    def get_anion_types(self):
        return self.anion_types

    def get_nn_info(self, structure: Structure, n: int, use_weights: bool = False) -> dict:
        """Get coordination number, CN, of site with index n in structure.

        Args:
            structure (Structure): input structure.
            n (int): index of site for which to determine CN.
            use_weights (bool): flag indicating whether (True)
                to use weights for computing the coordination number
                or not (False, default: each coordinated site has equal
                weight).
                True is not implemented for LobsterNeighbors

        Raises:
            ValueError: if use_weights is True or if structure passed and structure used to
                initialize LobsterNeighbors have different lengths.

        Returns:
            dict[str, Any]: coordination number and a list of nearest neighbors.
        """
        if use_weights:
            raise ValueError("LobsterEnv cannot use weights")
        if len(structure) != len(self.structure):
            raise ValueError(
                f"Length of structure ({len(structure)}) and LobsterNeighbors ({len(self.structure)}) differ"
            )
        return self.sg_list[n]  # type: ignore[return-value]

    def get_light_structure_environment(self, only_cation_environments=False, only_indices=None):
        """Get a LobsterLightStructureEnvironments object
        if the structure only contains coordination environments smaller 13.

        Args:
            only_cation_environments: only data for cations will be returned
            only_indices: will only evaluate the list of isites in this list

        Returns:
            LobsterLightStructureEnvironments
        """
        lgf = LocalGeometryFinder()
        lgf.setup_structure(structure=self.structure)
        list_ce_symbols = []
        list_csm = []
        list_permut = []
        for ival, _neigh_coords in enumerate(self.list_coords):
            if (len(_neigh_coords)) > 13:
                raise ValueError("Environment cannot be determined. Number of neighbors is larger than 13.")
            # to avoid problems if _neigh_coords is empty
            if _neigh_coords != []:
                lgf.setup_local_geometry(isite=ival, coords=_neigh_coords, optimization=2)
                cncgsm = lgf.get_coordination_symmetry_measures(optimization=2)
                list_ce_symbols.append(min(cncgsm.items(), key=lambda t: t[1]["csm_wcs_ctwcc"])[0])
                list_csm.append(min(cncgsm.items(), key=lambda t: t[1]["csm_wcs_ctwcc"])[1]["csm_wcs_ctwcc"])
                list_permut.append(min(cncgsm.items(), key=lambda t: t[1]["csm_wcs_ctwcc"])[1]["indices"])
            else:
                list_ce_symbols.append(None)
                list_csm.append(None)
                list_permut.append(None)

        if only_indices is None:
            if not only_cation_environments:
                lse = LobsterLightStructureEnvironments.from_Lobster(
                    list_ce_symbol=list_ce_symbols,
                    list_csm=list_csm,
                    list_permutation=list_permut,
                    list_neighsite=self.list_neighsite,
                    list_neighisite=self.list_neighisite,
                    structure=self.structure,
                    valences=self.valences,
                )
            else:
                new_list_ce_symbols = []
                new_list_csm = []
                new_list_permut = []
                new_list_neighsite = []
                new_list_neighisite = []

                for ival, val in enumerate(self.valences):
                    if val >= 0.0:
                        new_list_ce_symbols.append(list_ce_symbols[ival])
                        new_list_csm.append(list_csm[ival])
                        new_list_permut.append(list_permut[ival])
                        new_list_neighisite.append(self.list_neighisite[ival])
                        new_list_neighsite.append(self.list_neighsite[ival])
                    else:
                        new_list_ce_symbols.append(None)
                        new_list_csm.append(None)
                        new_list_permut.append([])
                        new_list_neighisite.append([])
                        new_list_neighsite.append([])

                lse = LobsterLightStructureEnvironments.from_Lobster(
                    list_ce_symbol=new_list_ce_symbols,
                    list_csm=new_list_csm,
                    list_permutation=new_list_permut,
                    list_neighsite=new_list_neighsite,
                    list_neighisite=new_list_neighisite,
                    structure=self.structure,
                    valences=self.valences,
                )
        else:
            new_list_ce_symbols = []
            new_list_csm = []
            new_list_permut = []
            new_list_neighsite = []
            new_list_neighisite = []

            for isite, _site in enumerate(self.structure):
                if isite in only_indices:
                    new_list_ce_symbols.append(list_ce_symbols[isite])
                    new_list_csm.append(list_csm[isite])
                    new_list_permut.append(list_permut[isite])
                    new_list_neighisite.append(self.list_neighisite[isite])
                    new_list_neighsite.append(self.list_neighsite[isite])
                else:
                    new_list_ce_symbols.append(None)
                    new_list_csm.append(None)
                    new_list_permut.append([])
                    new_list_neighisite.append([])
                    new_list_neighsite.append([])

            lse = LobsterLightStructureEnvironments.from_Lobster(
                list_ce_symbol=new_list_ce_symbols,
                list_csm=new_list_csm,
                list_permutation=new_list_permut,
                list_neighsite=new_list_neighsite,
                list_neighisite=new_list_neighisite,
                structure=self.structure,
                valences=self.valences,
            )

        return lse

    def get_info_icohps_to_neighbors(self, isites=None, onlycation_isites=True):
        """Get information on the icohps of neighbors for certain sites as identified by their site id.
        This is useful for plotting the relevant cohps of a site in the structure.
        (could be ICOOPLIST.lobster or ICOHPLIST.lobster or ICOBILIST.lobster).

        Args:
            isites: list of site ids. If isite==None, all isites will be used to add the icohps of the neighbors
            onlycation_isites: if True and if isite==None, it will only analyse the sites of the cations

        Returns:
            ICOHPNeighborsInfo
        """
        if self.valences is None and onlycation_isites:
            raise ValueError("No valences are provided")
        if isites is None:
            if onlycation_isites:
                isites = [i for i in range(len(self.structure)) if self.valences[i] >= 0.0]
            else:
                isites = list(range(len(self.structure)))

        summed_icohps = 0.0
        list_icohps = []
        number_bonds = 0
        labels = []
        atoms = []
        final_isites = []
        for ival, _site in enumerate(self.structure):
            if ival in isites:
                for keys, icohpsum in zip(self.list_keys[ival], self.list_icohps[ival]):
                    summed_icohps += icohpsum
                    list_icohps.append(icohpsum)
                    labels.append(keys)
                    atoms.append(
                        [
                            self.Icohpcollection._list_atom1[int(keys) - 1],
                            self.Icohpcollection._list_atom2[int(keys) - 1],
                        ]
                    )
                    number_bonds += 1
                    final_isites.append(ival)
        return ICOHPNeighborsInfo(summed_icohps, list_icohps, number_bonds, labels, atoms, final_isites)

    def plot_cohps_of_neighbors(
        self,
        path_to_cohpcar: str | None = "COHPCAR.lobster",
        obj_cohpcar: CompleteCohp | None = None,
        isites: list[int] | None = None,
        onlycation_isites: bool = True,
        only_bonds_to: list[str] | None = None,
        per_bond: bool = False,
        summed_spin_channels: bool = False,
        xlim=None,
        ylim=(-10, 6),
        integrated: bool = False,
    ):
        """
        Will plot summed cohps or cobis or coops
        (please be careful in the spin polarized case (plots might overlap (exactly!)).

        Args:
            path_to_cohpcar: str, path to COHPCAR or COOPCAR or COBICAR
            obj_cohpcar: CompleteCohp object
            isites: list of site ids, if isite==[], all isites will be used to add the icohps of the neighbors
            onlycation_isites: bool, will only use cations, if isite==[]
            only_bonds_to: list of str, only anions in this list will be considered
            per_bond: bool, will lead to a normalization of the plotted COHP per number of bond if True,
            otherwise the sum
            will be plotted
            xlim: list of float, limits of x values
            ylim: list of float, limits of y values
            integrated: bool, if true will show integrated cohp instead of cohp

        Returns:
            plt of the cohps or coops or cobis
        """
        # include COHPPlotter and plot a sum of these COHPs
        # might include option to add Spin channels
        # implement only_bonds_to
        cp = CohpPlotter(are_cobis=self.are_cobis, are_coops=self.are_coops)

        plotlabel, summed_cohp = self.get_info_cohps_to_neighbors(
            path_to_cohpcar,
            obj_cohpcar,
            isites,
            only_bonds_to,
            onlycation_isites,
            per_bond,
            summed_spin_channels=summed_spin_channels,
        )

        cp.add_cohp(plotlabel, summed_cohp)
        ax = cp.get_plot(integrated=integrated)
        if xlim is not None:
            ax.set_xlim(xlim)

        if ylim is not None:
            ax.set_ylim(ylim)

        return ax

    def get_info_cohps_to_neighbors(
        self,
        path_to_cohpcar: str | None = "COHPCAR.lobster",
        obj_cohpcar: CompleteCohp | None = None,
        isites: list[int] | None = None,
        only_bonds_to: list[str] | None = None,
        onlycation_isites: bool = True,
        per_bond: bool = True,
        summed_spin_channels: bool = False,
    ):
        """Get info about the cohps (coops or cobis) as a summed cohp object and a label
        from all sites mentioned in isites with neighbors.

        Args:
            path_to_cohpcar: str, path to COHPCAR or COOPCAR or COBICAR
            obj_cohpcar: CompleteCohp object
            isites: list of int that indicate the number of the site
            only_bonds_to: list of str, e.g. ["O"] to only show cohps of anything to oxygen
            onlycation_isites: if isites=None, only cation sites will be returned
            per_bond: will normalize per bond
            summed_spin_channels: will sum all spin channels

        Returns:
            str: label for COHP, CompleteCohp object which describes all cohps (coops or cobis)
                of the sites as given by isites and the other parameters
        """
        # TODO: add options for orbital-resolved cohps
        _summed_icohps, _list_icohps, _number_bonds, labels, atoms, final_isites = self.get_info_icohps_to_neighbors(
            isites=isites, onlycation_isites=onlycation_isites
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = f"{tmp_dir}/POSCAR.vasp"

            self.structure.to(filename=path, fmt="poscar")

            if not hasattr(self, "completecohp"):
                if path_to_cohpcar is not None and obj_cohpcar is None:
                    self.completecohp = CompleteCohp.from_file(
                        fmt="LOBSTER",
                        filename=path_to_cohpcar,
                        structure_file=path,
                        are_coops=self.are_coops,
                        are_cobis=self.are_cobis,
                    )
                elif obj_cohpcar is not None:
                    self.completecohp = obj_cohpcar
                else:
                    raise ValueError("Please provide either path_to_cohpcar or obj_cohpcar")

        # will check that the number of bonds in ICOHPLIST and COHPCAR are identical
        # further checks could be implemented
        if len(self.Icohpcollection._list_atom1) != len(self.completecohp.bonds):
            raise ValueError("COHPCAR and ICOHPLIST do not fit together")
        is_spin_completecohp = Spin.down in self.completecohp.get_cohp_by_label("1").cohp
        if self.Icohpcollection.is_spin_polarized != is_spin_completecohp:
            raise ValueError("COHPCAR and ICOHPLIST do not fit together")

        if only_bonds_to is None:
            # sort by anion type
            divisor = len(labels) if per_bond else 1

            plot_label = self._get_plot_label(atoms, per_bond)
            summed_cohp = self.completecohp.get_summed_cohp_by_label_list(
                label_list=labels,
                divisor=divisor,
                summed_spin_channels=summed_spin_channels,
            )

        else:
            # labels of the COHPs that will be summed!
            # iterate through labels and atoms and check which bonds can be included
            new_labels = []
            new_atoms = []
            for key, atompair, isite in zip(labels, atoms, final_isites):
                present = False
                for atomtype in only_bonds_to:
                    # This is necessary to identify also bonds between the same elements correctly!
                    if str(self.structure[isite].species.elements[0]) != atomtype:
                        if atomtype in (
                            self._split_string(atompair[0])[0],
                            self._split_string(atompair[1])[0],
                        ):
                            present = True
                    elif (
                        atomtype == self._split_string(atompair[0])[0]
                        and atomtype == self._split_string(atompair[1])[0]
                    ):
                        present = True

                if present:
                    new_labels.append(key)
                    new_atoms.append(atompair)
            if new_labels:
                divisor = len(new_labels) if per_bond else 1

                plot_label = self._get_plot_label(new_atoms, per_bond)
                summed_cohp = self.completecohp.get_summed_cohp_by_label_list(
                    label_list=new_labels,
                    divisor=divisor,
                    summed_spin_channels=summed_spin_channels,
                )
            else:
                plot_label = None

                summed_cohp = None

        return plot_label, summed_cohp

    def _get_plot_label(self, atoms, per_bond):
        # count the types of bonds and append a label:
        all_labels = []
        for atoms_names in atoms:
            new = [self._split_string(atoms_names[0])[0], self._split_string(atoms_names[1])[0]]
            new.sort()
            string_here = f"{new[0]}-{new[1]}"
            all_labels.append(string_here)
        count = collections.Counter(all_labels)
        plotlabels = []
        for key, item in count.items():
            plotlabels.append(f"{item} x {key}")
        label = ", ".join(plotlabels)
        if per_bond:
            label += " (per bond)"
        return label

    def get_info_icohps_between_neighbors(self, isites=None, onlycation_isites=True):
        """Get infos about interactions between neighbors of a certain atom.

        Args:
            isites: list of site ids, if isite==None, all isites will be used
            onlycation_isites: will only use cations, if isite==None

        Returns:
            ICOHPNeighborsInfo
        """
        lowerlimit = self.lowerlimit
        upperlimit = self.upperlimit

        if self.valences is None and onlycation_isites:
            raise ValueError("No valences are provided")
        if isites is None:
            if onlycation_isites:
                isites = [i for i in range(len(self.structure)) if self.valences[i] >= 0.0]
            else:
                isites = list(range(len(self.structure)))

        summed_icohps = 0.0
        list_icohps = []
        number_bonds = 0
        labels = []
        atoms = []
        for isite in isites:
            for in_site, n_site in enumerate(self.list_neighsite[isite]):
                for in_site2, n_site2 in enumerate(self.list_neighsite[isite]):
                    if in_site < in_site2:
                        unitcell1 = self._determine_unit_cell(n_site)
                        unitcell2 = self._determine_unit_cell(n_site2)

                        index_n_site = self._get_original_site(self.structure, n_site)
                        index_n_site2 = self._get_original_site(self.structure, n_site2)

                        if index_n_site < index_n_site2:
                            translation = list(np.array(unitcell1) - np.array(unitcell2))
                        elif index_n_site2 < index_n_site:
                            translation = list(np.array(unitcell2) - np.array(unitcell1))
                        else:
                            translation = list(np.array(unitcell1) - np.array(unitcell2))

                        icohps = self._get_icohps(
                            icohpcollection=self.Icohpcollection,
                            isite=index_n_site,
                            lowerlimit=lowerlimit,
                            upperlimit=upperlimit,
                            only_bonds_to=self.only_bonds_to,
                        )

                        done = False
                        for icohp in icohps.values():
                            atomnr1 = self._get_atomnumber(icohp._atom1)
                            atomnr2 = self._get_atomnumber(icohp._atom2)
                            label = icohp._label

                            if (index_n_site == atomnr1 and index_n_site2 == atomnr2) or (
                                index_n_site == atomnr2 and index_n_site2 == atomnr1
                            ):
                                if atomnr1 != atomnr2:
                                    if np.all(np.asarray(translation) == np.asarray(icohp._translation)):
                                        summed_icohps += icohp.summed_icohp
                                        list_icohps.append(icohp.summed_icohp)
                                        number_bonds += 1
                                        labels.append(label)
                                        atoms.append(
                                            [
                                                self.Icohpcollection._list_atom1[int(label) - 1],
                                                self.Icohpcollection._list_atom2[int(label) - 1],
                                            ]
                                        )

                                elif not done:
                                    icohp_trans = -np.asarray(
                                        [icohp._translation[0], icohp._translation[1], icohp._translation[2]]
                                    )

                                    if (np.all(np.asarray(translation) == np.asarray(icohp._translation))) or (
                                        np.all(np.asarray(translation) == icohp_trans)
                                    ):
                                        summed_icohps += icohp.summed_icohp
                                        list_icohps.append(icohp.summed_icohp)
                                        number_bonds += 1
                                        labels.append(label)
                                        atoms.append(
                                            [
                                                self.Icohpcollection._list_atom1[int(label) - 1],
                                                self.Icohpcollection._list_atom2[int(label) - 1],
                                            ]
                                        )
                                        done = True

        return ICOHPNeighborsInfo(summed_icohps, list_icohps, number_bonds, labels, atoms, None)

    def _evaluate_ce(
        self,
        lowerlimit,
        upperlimit,
        only_bonds_to=None,
        additional_condition: int = 0,
        perc_strength_icohp: float = 0.15,
        adapt_extremum_to_add_cond: bool = False,
    ) -> None:
        """
        Args:
            lowerlimit: lower limit which determines the ICOHPs that are considered for the determination of the
            neighbors
            upperlimit: upper limit which determines the ICOHPs that are considered for the determination of the
            neighbors
            only_bonds_to: restricts the types of bonds that will be considered
            additional_condition: Additional condition for the evaluation
            perc_strength_icohp: will be used to determine how strong the ICOHPs (percentage*strongest ICOHP) will be
            that are still considered for the evaluation
            adapt_extremum_to_add_cond: will recalculate the limit based on the bonding type and not on the overall
            extremum.
        """
        # get extremum
        if lowerlimit is None and upperlimit is None:
            lowerlimit, upperlimit = self._get_limit_from_extremum(
                self.Icohpcollection,
                percentage=perc_strength_icohp,
                adapt_extremum_to_add_cond=adapt_extremum_to_add_cond,
                additional_condition=additional_condition,
            )

        elif upperlimit is None or lowerlimit is None:
            raise ValueError("Please give two limits or leave them both at None")

        # find environments based on ICOHP values
        list_icohps, list_keys, list_lengths, list_neighisite, list_neighsite, list_coords = self._find_environments(
            additional_condition, lowerlimit, upperlimit, only_bonds_to
        )

        self.list_icohps = list_icohps
        self.list_lengths = list_lengths
        self.list_keys = list_keys
        self.list_neighsite = list_neighsite
        self.list_neighisite = list_neighisite
        self.list_coords = list_coords

        # make a structure graph
        # make sure everything is relative to the given Structure and not just the atoms in the unit cell
        if self.add_additional_data_sg:
            self.sg_list = [
                [
                    {
                        "site": neighbor,
                        "image": tuple(
                            int(round(i))
                            for i in (
                                neighbor.frac_coords
                                - self.structure[
                                    next(
                                        isite
                                        for isite, site in enumerate(self.structure)
                                        if neighbor.is_periodic_image(site)
                                    )
                                ].frac_coords
                            )
                        ),
                        "weight": 1,
                        # Here, the ICOBIs and ICOOPs are added based on the bond
                        # strength cutoff of the ICOHP
                        # more changes are necessary here if we use icobis for cutoffs
                        "edge_properties": {
                            "ICOHP": self.list_icohps[ineighbors][ineighbor],
                            "bond_length": self.list_lengths[ineighbors][ineighbor],
                            "bond_label": self.list_keys[ineighbors][ineighbor],
                            self.id_blist_sg1.upper(): self.bonding_list_1.icohpcollection.get_icohp_by_label(
                                self.list_keys[ineighbors][ineighbor]
                            ),
                            self.id_blist_sg2.upper(): self.bonding_list_2.icohpcollection.get_icohp_by_label(
                                self.list_keys[ineighbors][ineighbor]
                            ),
                        },
                        "site_index": next(
                            isite for isite, site in enumerate(self.structure) if neighbor.is_periodic_image(site)
                        ),
                    }
                    for ineighbor, neighbor in enumerate(neighbors)
                ]
                for ineighbors, neighbors in enumerate(self.list_neighsite)
            ]
        else:
            self.sg_list = [
                [
                    {
                        "site": neighbor,
                        "image": tuple(
                            int(round(i))
                            for i in (
                                neighbor.frac_coords
                                - self.structure[
                                    next(
                                        isite
                                        for isite, site in enumerate(self.structure)
                                        if neighbor.is_periodic_image(site)
                                    )
                                ].frac_coords
                            )
                        ),
                        "weight": 1,
                        "edge_properties": {
                            "ICOHP": self.list_icohps[ineighbors][ineighbor],
                            "bond_length": self.list_lengths[ineighbors][ineighbor],
                            "bond_label": self.list_keys[ineighbors][ineighbor],
                        },
                        "site_index": next(
                            isite for isite, site in enumerate(self.structure) if neighbor.is_periodic_image(site)
                        ),
                    }
                    for ineighbor, neighbor in enumerate(neighbors)
                ]
                for ineighbors, neighbors in enumerate(self.list_neighsite)
            ]

    def _find_environments(self, additional_condition, lowerlimit, upperlimit, only_bonds_to):
        """
        Will find all relevant neighbors based on certain restrictions.

        Args:
            additional_condition (int): additional condition (see above)
            lowerlimit (float): lower limit that tells you which ICOHPs are considered
            upperlimit (float): upper limit that tells you which ICOHPs are considered
            only_bonds_to (list): list of str, e.g. ["O"] that will ensure that only bonds to "O" will be considered

        Returns:
            tuple: list of icohps, list of keys, list of lengths, list of neighisite, list of neighsite, list of coords
        """
        # run over structure
        list_neighsite = []
        list_neighisite = []
        list_coords = []
        list_icohps = []
        list_lengths = []
        list_keys = []
        for idx, site in enumerate(self.structure):
            icohps = self._get_icohps(
                icohpcollection=self.Icohpcollection,
                isite=idx,
                lowerlimit=lowerlimit,
                upperlimit=upperlimit,
                only_bonds_to=only_bonds_to,
            )

            additional_conds = self._find_relevant_atoms_additional_condition(idx, icohps, additional_condition)
            keys_from_ICOHPs, lengths_from_ICOHPs, neighbors_from_ICOHPs, selected_ICOHPs = additional_conds

            if len(neighbors_from_ICOHPs) > 0:
                centralsite = site

                neighbors_by_distance_start = self.structure.get_sites_in_sphere(
                    pt=centralsite.coords,
                    r=np.max(lengths_from_ICOHPs) + 0.5,
                    include_image=True,
                    include_index=True,
                )

                neighbors_by_distance = []
                list_distances = []
                index_here_list = []
                coords = []
                for neigh_new in sorted(neighbors_by_distance_start, key=lambda x: x[1]):
                    site_here = neigh_new[0].to_unit_cell()
                    index_here = neigh_new[2]
                    index_here_list.append(index_here)
                    cell_here = neigh_new[3]
                    new_coords = [
                        site_here.frac_coords[0] + float(cell_here[0]),
                        site_here.frac_coords[1] + float(cell_here[1]),
                        site_here.frac_coords[2] + float(cell_here[2]),
                    ]
                    coords.append(site_here.lattice.get_cartesian_coords(new_coords))

                    # new_site = PeriodicSite(
                    #     species=site_here.species_string,
                    #     coords=site_here.lattice.get_cartesian_coords(new_coords),
                    #     lattice=site_here.lattice,
                    #     to_unit_cell=False,
                    #     coords_are_cartesian=True,
                    # )
                    neighbors_by_distance.append(neigh_new[0])
                    list_distances.append(neigh_new[1])
                _list_neighsite = []
                _list_neighisite = []
                copied_neighbors_from_ICOHPs = copy.copy(neighbors_from_ICOHPs)
                copied_distances_from_ICOHPs = copy.copy(lengths_from_ICOHPs)
                _neigh_coords = []
                _neigh_frac_coords = []

                for ineigh, neigh in enumerate(neighbors_by_distance):
                    index_here2 = index_here_list[ineigh]

                    for idist, dist in enumerate(copied_distances_from_ICOHPs):
                        if (
                            np.isclose(dist, list_distances[ineigh], rtol=1e-4)
                            and copied_neighbors_from_ICOHPs[idist] == index_here2
                        ):
                            _list_neighsite.append(neigh)
                            _list_neighisite.append(index_here2)
                            _neigh_coords.append(coords[ineigh])
                            _neigh_frac_coords.append(neigh.frac_coords)
                            del copied_distances_from_ICOHPs[idist]
                            del copied_neighbors_from_ICOHPs[idist]
                            break

                list_neighisite.append(_list_neighisite)
                list_neighsite.append(_list_neighsite)
                list_lengths.append(lengths_from_ICOHPs)
                list_keys.append(keys_from_ICOHPs)
                list_coords.append(_neigh_coords)
                list_icohps.append(selected_ICOHPs)

            else:
                list_neighsite.append([])
                list_neighisite.append([])
                list_icohps.append([])
                list_lengths.append([])
                list_keys.append([])
                list_coords.append([])
        return (
            list_icohps,
            list_keys,
            list_lengths,
            list_neighisite,
            list_neighsite,
            list_coords,
        )

    def _find_relevant_atoms_additional_condition(self, isite, icohps, additional_condition):
        """
        Will find all relevant atoms that fulfill the additional_conditions.

        Args:
            isite: number of site in structure (starts with 0)
            icohps: icohps
            additional_condition (int): additional condition

        Returns:
            tuple: keys, lengths and neighbors from selected ICOHPs and selected ICOHPs
        """
        neighbors_from_ICOHPs = []
        lengths_from_ICOHPs = []
        icohps_from_ICOHPs = []
        keys_from_ICOHPs = []

        for key, icohp in icohps.items():
            atomnr1 = self._get_atomnumber(icohp._atom1)
            atomnr2 = self._get_atomnumber(icohp._atom2)

            # test additional conditions
            val1 = val2 = None
            if additional_condition in (1, 3, 5, 6):
                val1 = self.valences[atomnr1]
                val2 = self.valences[atomnr2]

            if additional_condition == 0:
                # NO_ADDITIONAL_CONDITION
                if atomnr1 == isite:
                    neighbors_from_ICOHPs.append(atomnr2)
                    lengths_from_ICOHPs.append(icohp._length)
                    icohps_from_ICOHPs.append(icohp.summed_icohp)
                    keys_from_ICOHPs.append(key)
                elif atomnr2 == isite:
                    neighbors_from_ICOHPs.append(atomnr1)
                    lengths_from_ICOHPs.append(icohp._length)
                    icohps_from_ICOHPs.append(icohp.summed_icohp)
                    keys_from_ICOHPs.append(key)

            elif additional_condition == 1:
                # ONLY_ANION_CATION_BONDS
                if (val1 < 0.0 < val2) or (val2 < 0.0 < val1):
                    if atomnr1 == isite:
                        neighbors_from_ICOHPs.append(atomnr2)
                        lengths_from_ICOHPs.append(icohp._length)
                        icohps_from_ICOHPs.append(icohp.summed_icohp)
                        keys_from_ICOHPs.append(key)

                    elif atomnr2 == isite:
                        neighbors_from_ICOHPs.append(atomnr1)
                        lengths_from_ICOHPs.append(icohp._length)
                        icohps_from_ICOHPs.append(icohp.summed_icohp)
                        keys_from_ICOHPs.append(key)

            elif additional_condition == 2:
                # NO_ELEMENT_TO_SAME_ELEMENT_BONDS
                if icohp._atom1.rstrip("0123456789") != icohp._atom2.rstrip("0123456789"):
                    if atomnr1 == isite:
                        neighbors_from_ICOHPs.append(atomnr2)
                        lengths_from_ICOHPs.append(icohp._length)
                        icohps_from_ICOHPs.append(icohp.summed_icohp)
                        keys_from_ICOHPs.append(key)

                    elif atomnr2 == isite:
                        neighbors_from_ICOHPs.append(atomnr1)
                        lengths_from_ICOHPs.append(icohp._length)
                        icohps_from_ICOHPs.append(icohp.summed_icohp)
                        keys_from_ICOHPs.append(key)

            elif additional_condition == 3:
                # ONLY_ANION_CATION_BONDS_AND_NO_ELEMENT_TO_SAME_ELEMENT_BONDS = 3
                if ((val1 < 0.0 < val2) or (val2 < 0.0 < val1)) and icohp._atom1.rstrip(
                    "0123456789"
                ) != icohp._atom2.rstrip("0123456789"):
                    if atomnr1 == isite:
                        neighbors_from_ICOHPs.append(atomnr2)
                        lengths_from_ICOHPs.append(icohp._length)
                        icohps_from_ICOHPs.append(icohp.summed_icohp)
                        keys_from_ICOHPs.append(key)

                    elif atomnr2 == isite:
                        neighbors_from_ICOHPs.append(atomnr1)
                        lengths_from_ICOHPs.append(icohp._length)
                        icohps_from_ICOHPs.append(icohp.summed_icohp)
                        keys_from_ICOHPs.append(key)

            elif additional_condition == 4:
                # ONLY_ELEMENT_TO_OXYGEN_BONDS = 4
                if icohp._atom1.rstrip("0123456789") == "O" or icohp._atom2.rstrip("0123456789") == "O":
                    if atomnr1 == isite:
                        neighbors_from_ICOHPs.append(atomnr2)
                        lengths_from_ICOHPs.append(icohp._length)
                        icohps_from_ICOHPs.append(icohp.summed_icohp)
                        keys_from_ICOHPs.append(key)

                    elif atomnr2 == isite:
                        neighbors_from_ICOHPs.append(atomnr1)
                        lengths_from_ICOHPs.append(icohp._length)
                        icohps_from_ICOHPs.append(icohp.summed_icohp)
                        keys_from_ICOHPs.append(key)

            elif additional_condition == 5:
                # DO_NOT_CONSIDER_ANION_CATION_BONDS=5
                if (val1 > 0.0 and val2 > 0.0) or (val1 < 0.0 and val2 < 0.0):
                    if atomnr1 == isite:
                        neighbors_from_ICOHPs.append(atomnr2)
                        lengths_from_ICOHPs.append(icohp._length)
                        icohps_from_ICOHPs.append(icohp.summed_icohp)
                        keys_from_ICOHPs.append(key)

                    elif atomnr2 == isite:
                        neighbors_from_ICOHPs.append(atomnr1)
                        lengths_from_ICOHPs.append(icohp._length)
                        icohps_from_ICOHPs.append(icohp.summed_icohp)
                        keys_from_ICOHPs.append(key)

            elif additional_condition == 6 and val1 > 0.0 and val2 > 0.0:
                # ONLY_CATION_CATION_BONDS=6
                if atomnr1 == isite:
                    neighbors_from_ICOHPs.append(atomnr2)
                    lengths_from_ICOHPs.append(icohp._length)
                    icohps_from_ICOHPs.append(icohp.summed_icohp)
                    keys_from_ICOHPs.append(key)

                elif atomnr2 == isite:
                    neighbors_from_ICOHPs.append(atomnr1)
                    lengths_from_ICOHPs.append(icohp._length)
                    icohps_from_ICOHPs.append(icohp.summed_icohp)
                    keys_from_ICOHPs.append(key)

        return keys_from_ICOHPs, lengths_from_ICOHPs, neighbors_from_ICOHPs, icohps_from_ICOHPs

    @staticmethod
    def _get_icohps(icohpcollection, isite, lowerlimit, upperlimit, only_bonds_to):
        """Return icohp dict for certain site.

        Args:
            icohpcollection: Icohpcollection object
            isite (int): number of a site
            lowerlimit (float): lower limit that tells you which ICOHPs are considered
            upperlimit (float): upper limit that tells you which ICOHPs are considered
            only_bonds_to (list): list of str, e.g. ["O"] that will ensure that only bonds to "O" will be considered

        Returns:
            dict: of IcohpValues. The keys correspond to the values from the initial list_labels.
        """
        return icohpcollection.get_icohp_dict_of_site(
            site=isite,
            maxbondlength=6.0,
            minsummedicohp=lowerlimit,
            maxsummedicohp=upperlimit,
            only_bonds_to=only_bonds_to,
        )

    @staticmethod
    def _get_atomnumber(atomstring) -> int:
        """Get the number of the atom within the initial POSCAR (e.g., Return 0 for "Na1").

        Args:
            atomstring: string such as "Na1"

        Returns:
            int: indicating the position in the POSCAR
        """
        return int(LobsterNeighbors._split_string(atomstring)[1]) - 1

    @staticmethod
    def _split_string(s) -> tuple[str, str]:
        """
        Will split strings such as "Na1" in "Na" and "1" and return "1".

        Args:
            s (str): string
        """
        head = s.rstrip("0123456789")
        tail = s[len(head) :]
        return head, tail

    @staticmethod
    def _determine_unit_cell(site):
        """
        Based on the site it will determine the unit cell, in which this site is based.

        Args:
            site: site object
        """
        unitcell = []
        for coord in site.frac_coords:
            value = math.floor(round(coord, 4))
            unitcell.append(value)

        return unitcell

    def _adapt_extremum_to_add_cond(self, list_icohps, percentage):
        """
        Convinicence method for returning the extremum of the given icohps or icoops or icobis list.

        Args:
            list_icohps: can be a list of icohps or icobis or icobis

        Returns:
            float: min value of input list of icohps / max value of input list of icobis or icobis
        """
        which_extr = min if not self.are_coops and not self.are_cobis else max
        return which_extr(list_icohps) * percentage

    def _get_limit_from_extremum(
        self,
        icohpcollection,
        percentage=0.15,
        adapt_extremum_to_add_cond=False,
        additional_condition=0,
    ):
        """Get limits for the evaluation of the icohp values from an icohpcollection
        Return -float("inf"), min(max_icohp*0.15,-0.1). Currently only works for ICOHPs.

        Args:
            icohpcollection: icohpcollection object
            percentage: will determine which ICOHPs or ICOOP or ICOBI will be considered
            (only 0.15 from the maximum value)
            adapt_extremum_to_add_cond: should the extrumum be adapted to the additional condition
            additional_condition: additional condition to determine which bonds are relevant

        Returns:
            tuple[float, float]: [-inf, min(strongest_icohp*0.15,-noise_cutoff)] / [max(strongest_icohp*0.15,
                noise_cutoff), inf]
        """
        extremum_based = None

        if not adapt_extremum_to_add_cond or additional_condition == 0:
            extremum_based = icohpcollection.extremum_icohpvalue(summed_spin_channels=True) * percentage
        elif additional_condition == 1:
            # only cation anion bonds
            list_icohps = []
            for value in icohpcollection._icohplist.values():
                atomnr1 = LobsterNeighbors._get_atomnumber(value._atom1)
                atomnr2 = LobsterNeighbors._get_atomnumber(value._atom2)

                val1 = self.valences[atomnr1]
                val2 = self.valences[atomnr2]
                if (val1 < 0.0 < val2) or (val2 < 0.0 < val1):
                    list_icohps.append(value.summed_icohp)

            extremum_based = self._adapt_extremum_to_add_cond(list_icohps, percentage)

        elif additional_condition == 2:
            # NO_ELEMENT_TO_SAME_ELEMENT_BONDS
            list_icohps = []
            for value in icohpcollection._icohplist.values():
                if value._atom1.rstrip("0123456789") != value._atom2.rstrip("0123456789"):
                    list_icohps.append(value.summed_icohp)

            extremum_based = self._adapt_extremum_to_add_cond(list_icohps, percentage)

        elif additional_condition == 3:
            # ONLY_ANION_CATION_BONDS_AND_NO_ELEMENT_TO_SAME_ELEMENT_BONDS = 3
            list_icohps = []
            for value in icohpcollection._icohplist.values():
                atomnr1 = LobsterNeighbors._get_atomnumber(value._atom1)
                atomnr2 = LobsterNeighbors._get_atomnumber(value._atom2)
                val1 = self.valences[atomnr1]
                val2 = self.valences[atomnr2]

                if ((val1 < 0.0 < val2) or (val2 < 0.0 < val1)) and value._atom1.rstrip(
                    "0123456789"
                ) != value._atom2.rstrip("0123456789"):
                    list_icohps.append(value.summed_icohp)

            extremum_based = self._adapt_extremum_to_add_cond(list_icohps, percentage)

        elif additional_condition == 4:
            list_icohps = []
            for value in icohpcollection._icohplist.values():
                if value._atom1.rstrip("0123456789") == "O" or value._atom2.rstrip("0123456789") == "O":
                    list_icohps.append(value.summed_icohp)

            extremum_based = self._adapt_extremum_to_add_cond(list_icohps, percentage)

        elif additional_condition == 5:
            # DO_NOT_CONSIDER_ANION_CATION_BONDS=5
            list_icohps = []
            for value in icohpcollection._icohplist.values():
                atomnr1 = LobsterNeighbors._get_atomnumber(value._atom1)
                atomnr2 = LobsterNeighbors._get_atomnumber(value._atom2)
                val1 = self.valences[atomnr1]
                val2 = self.valences[atomnr2]

                if (val1 > 0.0 and val2 > 0.0) or (val1 < 0.0 and val2 < 0.0):
                    list_icohps.append(value.summed_icohp)

            extremum_based = self._adapt_extremum_to_add_cond(list_icohps, percentage)

        elif additional_condition == 6:
            # ONLY_CATION_CATION_BONDS=6
            list_icohps = []
            for value in icohpcollection._icohplist.values():
                atomnr1 = LobsterNeighbors._get_atomnumber(value._atom1)
                atomnr2 = LobsterNeighbors._get_atomnumber(value._atom2)
                val1 = self.valences[atomnr1]
                val2 = self.valences[atomnr2]

                if val1 > 0.0 and val2 > 0.0:
                    list_icohps.append(value.summed_icohp)

            extremum_based = self._adapt_extremum_to_add_cond(list_icohps, percentage)

        if not self.are_coops and not self.are_cobis:
            max_here = min(extremum_based, -self.noise_cutoff) if self.noise_cutoff is not None else extremum_based
            return -float("inf"), max_here
        if self.are_coops or self.are_cobis:
            min_here = max(extremum_based, self.noise_cutoff) if self.noise_cutoff is not None else extremum_based
            return min_here, float("inf")

        return None


class LobsterLightStructureEnvironments(LightStructureEnvironments):
    """Store LightStructureEnvironments based on Lobster outputs."""

    @classmethod
    def from_Lobster(
        cls,
        list_ce_symbol,
        list_csm,
        list_permutation,
        list_neighsite,
        list_neighisite,
        structure: Structure,
        valences=None,
    ) -> Self:
        """
        Will set up a LightStructureEnvironments from Lobster.

        Args:
            structure: Structure object
            list_ce_symbol: list of symbols for coordination environments
            list_csm: list of continuous symmetry measures
            list_permutation: list of permutations
            list_neighsite: list of neighboring sites
            list_neighisite: list of neighboring isites (number of a site)
            valences: list of valences

        Returns:
            LobsterLightStructureEnvironments
        """
        strategy = None
        valences_origin = "user-defined"

        coordination_environments = []

        all_nbs_sites = []
        all_nbs_sites_indices = []
        neighbors_sets = []
        counter = 0
        for isite in range(len(structure)):
            # all_nbs_sites_here=[]
            all_nbs_sites_indices_here = []
            # Coordination environment
            if list_ce_symbol is not None:
                ce_dict = {
                    "ce_symbol": list_ce_symbol[isite],
                    "ce_fraction": 1.0,
                    "csm": list_csm[isite],
                    "permutation": list_permutation[isite],
                }
            else:
                ce_dict = None

            if list_neighisite[isite] is not None:
                for idx_neigh_site, neigh_site in enumerate(list_neighsite[isite]):
                    diff = neigh_site.frac_coords - structure[list_neighisite[isite][idx_neigh_site]].frac_coords
                    round_diff = np.round(diff)
                    if not np.allclose(diff, round_diff):
                        raise ValueError(
                            "Weird, differences between one site in a periodic image cell is not integer ..."
                        )
                    nb_image_cell = np.array(round_diff, int)

                    all_nbs_sites_indices_here.append(counter)

                    neighbor = {
                        "site": neigh_site,
                        "index": list_neighisite[isite][idx_neigh_site],
                        "image_cell": nb_image_cell,
                    }
                    all_nbs_sites.append(neighbor)
                    counter += 1

                all_nbs_sites_indices.append(all_nbs_sites_indices_here)
            else:
                all_nbs_sites.append({"site": None, "index": None, "image_cell": None})  # all_nbs_sites_here)
                all_nbs_sites_indices.append([])  # all_nbs_sites_indices_here)

            if list_neighisite[isite] is not None:
                nb_set = cls.NeighborsSet(
                    structure=structure,
                    isite=isite,
                    all_nbs_sites=all_nbs_sites,
                    all_nbs_sites_indices=all_nbs_sites_indices[isite],
                )

            else:
                nb_set = cls.NeighborsSet(
                    structure=structure,
                    isite=isite,
                    all_nbs_sites=[],
                    all_nbs_sites_indices=[],
                )

            coordination_environments.append([ce_dict])
            neighbors_sets.append([nb_set])

        return cls(
            strategy=strategy,
            coordination_environments=coordination_environments,
            all_nbs_sites=all_nbs_sites,
            neighbors_sets=neighbors_sets,
            structure=structure,
            valences=valences,
            valences_origin=valences_origin,
        )

    @property
    def uniquely_determines_coordination_environments(self):
        """True if the coordination environments are uniquely determined."""
        return True

    def as_dict(self):
        """
        Bson-serializable dict representation of the LightStructureEnvironments object.

        Returns:
            Bson-serializable dict representation of the LightStructureEnvironments object.
        """
        return {
            "@module": type(self).__module__,
            "@class": type(self).__name__,
            "strategy": self.strategy,
            "structure": self.structure.as_dict(),
            "coordination_environments": self.coordination_environments,
            "all_nbs_sites": [
                {
                    "site": nb_site["site"].as_dict(),
                    "index": nb_site["index"],
                    "image_cell": [int(ii) for ii in nb_site["image_cell"]],
                }
                for nb_site in self._all_nbs_sites
            ],
            "neighbors_sets": [
                [nb_set.as_dict() for nb_set in site_nb_sets] or None for site_nb_sets in self.neighbors_sets
            ],
            "valences": self.valences,
        }


class ICOHPNeighborsInfo(NamedTuple):
    """
    Tuple to represent information on relevant bonds
    Args:
        total_icohp (float): sum of icohp values of neighbors to the selected sites [given by the id in structure]
        list_icohps (list): list of summed icohp values for all identified interactions with neighbors
        n_bonds (int): number of identified bonds to the selected sites
        labels (list[str]): labels (from ICOHPLIST) for all identified bonds
        atoms (list[list[str]]): list of list describing the species present in the identified interactions
            (names from ICOHPLIST), e.g. ["Ag3", "O5"]
        central_isites (list[int]): list of the central isite for each identified interaction.
    """

    total_icohp: float
    list_icohps: list[float]
    n_bonds: int
    labels: list[str]
    atoms: list[list[str]]
    central_isites: list[int] | None
