import contextlib, io, pathlib, sys, unittest
root=pathlib.Path(__file__).resolve().parent
log=io.StringIO()
with contextlib.redirect_stdout(log),contextlib.redirect_stderr(log):
    result=unittest.TextTestRunner(stream=log,verbosity=2).run(unittest.defaultTestLoader.discover(str(root)))
(root.parent/'test-results.txt').write_text(log.getvalue(),encoding='utf-8')
print('Tests:',result.testsRun,'Failures:',len(result.failures),'Errors:',len(result.errors))
if not result.wasSuccessful(): print(log.getvalue())
sys.exit(not result.wasSuccessful())
