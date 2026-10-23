import ast,unittest
from pathlib import Path
from app.services.recruitment_rules import MAP_MODES,TASKS,GOALS
from app.schemas.time_slots import TIME_SLOT_OPTIONS
class IdentifierContractTests(unittest.TestCase):
 def test_domain_values_are_stable_ascii_identifiers(self):
  for value in [*MAP_MODES,*TASKS,*GOALS,*TIME_SLOT_OPTIONS,*[difficulty for values in MAP_MODES.values() for difficulty in values]]:
   self.assertTrue(value.isascii(),value);self.assertIn('.',value)
 def test_active_matching_never_compares_chinese_display_text(self):
  root=Path(__file__).resolve().parents[1]
  for relative in ['app/services/pilot.py','app/services/recruitment_rules.py','app/services/growth.py','app/services/phone_auth.py','app/services/simulation.py','app/schemas/recruitment_v4.py']:
   for node in ast.walk(ast.parse((root/relative).read_text())):
    if isinstance(node,ast.Compare):
     for item in ast.walk(node):
      if isinstance(item,ast.Constant) and isinstance(item.value,str):self.assertFalse(any('\u3400'<=c<='\u9fff' for c in item.value),f'{relative}:{node.lineno}')
