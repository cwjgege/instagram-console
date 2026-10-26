import sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path('tools').resolve()))
from patch_wda_location import patch
SOURCE='''@implementation FBCustomCommands
+ (NSArray *)routes {
    [[FBRoute GET:@"/wda/device/location"] respondWithTarget:self action:@selector(handleGetLocation:)],
}
+ (id<FBResponsePayload>)handleGetLocation:(FBRouteRequest *)request
{ return FBResponseWithObject(@{@"latitude": @0}); }
+ (id<FBResponsePayload>)handleNext:(FBRouteRequest *)request
{ return FBResponseWithOK(); }
'''
class LocationPatch(unittest.TestCase):
 def test_patch_preserves_other_commands_and_is_idempotent(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);file=root/'WebDriverAgentLib/Commands/FBCustomCommands.m';file.parent.mkdir(parents=True);file.write_text(SOURCE)
   self.assertTrue(patch(root));once=file.read_text();self.assertFalse(patch(root));self.assertEqual(file.read_text(),once)
   for required in ('handleNext','IGLocationReadbackDelegate','horizontalAccuracy','timestamp','deviceFingerprint','accuracyAuthorization','stopUpdatingLocation','[NSNull null]','locationServicesEnabled'):self.assertIn(required,once)
   self.assertEqual(once.count('+ (id<FBResponsePayload>)handleStopLocationReadback:'),1)
