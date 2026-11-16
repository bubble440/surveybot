from __future__ import annotations

"""Fixtures RÉELLES de l'oracle d'extraction BDL-4 (étape 4, critère C6).

Blocs tirés de collectes réelles (extraction_pass.json), anonymisés : URL retirée,
nom de client retiré des identifiants de champ. Le verdict attendu est celui
validé par l'annotation manuelle (colonne `truth`) : chaque test vérifie que
l'oracle ne contredit pas la vérité terrain observée.
"""

import unittest

from Survey.extraction_oracle import (
    INCONCLUSIVE, PASS, evaluate_extraction_pass,
)

# text — annotation : OK
BLOCK_TEXT = {
    "itype": "text", "question": "What is your age? * This question is required.",
    "min_select": 1, "max_select": 1, "target_id": "single_f6a05c84f32e",
    "options": [], "options_count": 0,
    "context": {"kind": "single", "tag": "input", "name": "sgE-9089576-8-27",
                "id": "sgE-9089576-8-27-element", "role": None, "native_date_input": False,
                "ifop_zip2city_widget": False, "surveyjs_sd_dropdown_widget": False},
}

# checkbox (min 3 / max 5, 15 options) — annotation : OK
BLOCK_CHECKBOX = {
    "itype": "checkbox",
    "question": "Which, if any, of the following sports or entertainment categories do you "
                "typically watch (i.e., streaming, live etc)? Select all that apply. * This question is required.",
    "min_select": 3, "max_select": 5, "target_id": "group_31c117252097",
    "options": ["Football (American)", "Basketball", "Tennis", "Fitness or training creators/coaches",
                "Baseball", "Golf", "Reality TV", "Health, wellness, or longevity creators/influencers",
                "Cricket", "Soccer", "Cycling", "Formula One/Motorsports", "Rugby",
                "Other (please specify):", "I do not watch any of these"],
    "options_count": 15,
    "context": {"kind": "group", "group_key": "checkbox:name:sge-9089576-13-34"},
}

# Page à 5 blocs (2 dropdown, 2 radio, 1 text) — annotation : OK sur les 5
PAGE_MULTI_BLOCKS = [
    {"itype": "dropdown", "question": "Quel est votre âge?", "min_select": 1, "max_select": 1,
     "target_id": "single_2b7d42e9cf93",
     # liste réelle : « Moins de 13 ans », 13 à 99, « 100 ou plus » (89 options)
     "options": ["Moins de 13 ans"] + [str(n) for n in range(13, 100)] + ["100 ou plus"],
     "options_count": 89,
     "context": {"kind": "single", "tag": "select", "name": "age", "id": "age", "role": None}},
    {"itype": "dropdown",
     "question": "Lequel des énoncés suivants décrit le mieux le domaine d'activité dans lequel vous travaillez "
                 "actuellement ? Veuillez répondre en fonction du travail que vous effectuez la plupart du temps, "
                 "quel que soit le secteur d'activité de votre employeur.",
     "min_select": 1, "max_select": 1, "target_id": "single_ac66f3fd768c",
     "options": ["Finance, comptabilité ou trésorerie", "Fiscalité", "Juridique",
                 "Conformité ou affaires réglementaires", "Marketing, publicité ou communication",
                 "Service client ou assistance", "Autre", "Je ne suis pas employé(e)"],
     "options_count": 14,  # total réel ; liste d'options abrégée (8 sur 14) : le verdict dropdown ne dépend que de l'itype
     "context": {"kind": "single", "tag": "select", "name": "line_of_business_fr",
                 "id": "line_of_business_fr", "role": None}},
    {"itype": "radio", "question": "Êtes-vous de genre masculin ou féminin?", "min_select": 1, "max_select": 1,
     "target_id": "group_caff86bb8d56", "options": ["Masculin", "Féminin"], "options_count": 2,
     "context": {"kind": "group", "group_key": "radio:name:gender"}},
    {"itype": "text", "question": "Quel est ton code postal?", "min_select": 1, "max_select": 1,
     "target_id": "single_5b1cb3b1b437", "options": [], "options_count": 0,
     "context": {"kind": "single", "tag": "input", "name": "postcode", "id": "postcode", "role": None}},
    {"itype": "radio", "question": "Qu’est-ce qui décrit le mieux votre rôle dans l’entreprise?",
     "min_select": 1, "max_select": 1, "target_id": "group_af6cbcb0b666",
     "options": ["Entrepreneur au niveau de la direction", "Vice-président", "Directeur", "Responsable",
                 "Assistant ou associé", "Autre", "Au chômage", "Je ne sais pas/aucun avis"],
     "options_count": 8, "context": {"kind": "group", "group_key": "radio:name:job_role"}},
]

# radio Confirmit/Wix — annotation : BAD (la question extraite est l'identifiant interne « qHab »,
# la vraie question affichée est « Habitez-vous bien à Paris 1er ? »).
BLOCK_QHAB_BAD = {
    "itype": "radio", "question": "qHab", "min_select": 1, "max_select": 1,
    "target_id": "group_4693e96dbc9e", "options": ["Oui", "Je me suis trompé(e) de code postal"],
    "options_count": 2,
    "context": {"kind": "group", "group_key": "radio:name:qHab", "confirmit_wix_fieldset_radio": True},
}


def _verdicts(blocks: list) -> list:
    out = evaluate_extraction_pass({"blocks": blocks})
    return [(b["verdict"], b.get("reason")) for b in out["blocks"]]


class ExtractionOracleRealFixtureTests(unittest.TestCase):
    def test_real_text_block_passes(self) -> None:
        self.assertEqual(_verdicts([BLOCK_TEXT]), [(PASS, None)])

    def test_real_checkbox_block_passes(self) -> None:
        self.assertEqual(_verdicts([BLOCK_CHECKBOX]), [(PASS, None)])

    def test_real_multi_block_page(self) -> None:
        # Dropdown : abstention structurelle ; radio et text sains : PASS.
        self.assertEqual(_verdicts(PAGE_MULTI_BLOCKS), [
            (INCONCLUSIVE, "unsupported_itype"), (INCONCLUSIVE, "unsupported_itype"),
            (PASS, None), (PASS, None), (PASS, None),
        ])

    def test_known_blind_spot_question_text_is_internal_id(self) -> None:
        """ANGLE MORT CONNU (C4) : bloc annoté BAD, mais structurellement cohérent -> PASS.

        Si une règle « le texte de la question est un identifiant interne » est ajoutée
        un jour, ce test doit être mis à jour (le verdict attendu deviendra SUSPECTED).
        """
        self.assertEqual(_verdicts([BLOCK_QHAB_BAD]), [(PASS, None)])


if __name__ == "__main__":
    unittest.main()
