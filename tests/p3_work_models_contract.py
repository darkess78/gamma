import importlib.util
from pathlib import Path
import unittest

path=Path(__file__).resolve().parents[1]/'src/gamma/coordination/work_models.py'
spec=importlib.util.spec_from_file_location('work_models',path)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

def build():
    return {'mode':'build','goal':'A useful local tool','files':{'tool.py':'', 'check_tool.py':'pass'},
            'editable':['tool.py'],'checks':[{'name':'feature flow','module':'check_tool','kind':'feature'}]}

class Contract(unittest.TestCase):
    def test_round_trip(self):
        value=build(); self.assertEqual(mod.validate_spec(value),value)
        value['files']['tool.py']='changed'; self.assertNotEqual(mod.validate_spec(build()),value)
    def test_research_has_no_edits(self):
        self.assertEqual(mod.validate_spec({'mode':'research','goal':'Read this file','files':{'guide.md':'stdlib only'},'editable':[],'checks':[]})['mode'],'research')
        bad=build();bad['mode']='research'
        with self.assertRaises(ValueError):mod.validate_spec(bad)
    def test_paths(self):
        for name in ('../bad.py','/tmp/bad.py','C:/bad.py','a\\bad.py','a//b.py','a/./b.py','a/../b.py','sitecustomize.py','usercustomize.py','a.py\n','CON','a:thing.py','.env','config/app.local.toml'):
            with self.subTest(name=name), self.assertRaises(ValueError):mod.safe_relative(name)
        self.assertEqual(mod.safe_relative('src/tool.py'),'src/tool.py')
    def test_protected_checks(self):
        for mutation in ('missing','editable','unknown','extra','duplicates'):
            value=build()
            if mutation=='missing':value['files'].pop('check_tool.py')
            elif mutation=='editable':value['editable'].append('check_tool.py')
            elif mutation=='unknown':value['checks'][0]['kind']='shell'
            elif mutation=='extra':value['checks'][0]['command']='rm'
            else:value['editable'].append('tool.py')
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):mod.validate_spec(value)
    def test_types_and_limits(self):
        mutations=[('mode','apply'),('goal',''),('goal','x'*6001),('files',{'tool.py':True}),('files',{'tool.py':'x'*60001}),('editable','tool.py'),('checks',[]),('unknown',True)]
        for key,value in mutations:
            item=build();item[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):mod.validate_spec(item)

if __name__=='__main__':unittest.main()
