"""Add explicit, timestamped CoreLocation readback to bundled WDA sources."""
from pathlib import Path

METHOD=r'''+ (id<FBResponsePayload>)handleGetLocation:(FBRouteRequest *)request
{
#if TARGET_OS_TV || TARGET_OS_WATCH
  return FBResponseWithStatus([FBCommandStatus unsupportedOperationErrorWithMessage:@"unsupported" traceback:nil]);
#else
  // IG_LOCATION_READBACK_V1: never turn a nil CLLocation into coordinates (0, 0).
  if (nil == IGLocationManager) {
    IGLocationManager = [[CLLocationManager alloc] init];
    IGLocationDelegate = [IGLocationReadbackDelegate new];
    IGLocationManager.delegate = IGLocationDelegate;
    IGLocationManager.distanceFilter = kCLDistanceFilterNone;
    IGLocationManager.desiredAccuracy = kCLLocationAccuracyBest;
    IGLocationManager.pausesLocationUpdatesAutomatically = NO;
  }
  CLAuthorizationStatus auth = IGLocationManager.authorizationStatus;
  if (auth == kCLAuthorizationStatusNotDetermined) {
    [IGLocationManager requestAlwaysAuthorization];
  }
  __block NSNumber *servicesEnabled = nil;
  dispatch_async(dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{
    BOOL value = [CLLocationManager locationServicesEnabled];
    dispatch_async(dispatch_get_main_queue(), ^{ servicesEnabled = @(value); });
  });
  [[[FBRunLoopSpinner new] timeout:0.2] spinUntilTrue:^BOOL{ return servicesEnabled != nil; }];
  BOOL enabled = servicesEnabled.boolValue;
  if (enabled && (auth == kCLAuthorizationStatusAuthorizedAlways || auth == kCLAuthorizationStatusAuthorizedWhenInUse)) {
    [IGLocationManager startUpdatingLocation];
  }
  CLLocation *location = IGLocationDelegate.location;
  BOOL valid = location != nil && location.horizontalAccuracy >= 0 && CLLocationCoordinate2DIsValid(location.coordinate);
  return FBResponseWithObject(@{
    @"locationSchema": @1,
    @"deviceFingerprint": [UIDevice currentDevice].identifierForVendor.UUIDString ?: @"",
    @"authorizationStatus": @(auth),
    @"locationServicesEnabled": servicesEnabled ?: [NSNull null],
    @"accuracyAuthorization": @(IGLocationManager.accuracyAuthorization),
    @"valid": @(valid),
    @"latitude": valid ? @(location.coordinate.latitude) : [NSNull null],
    @"longitude": valid ? @(location.coordinate.longitude) : [NSNull null],
    @"timestamp": valid ? @([location.timestamp timeIntervalSince1970]) : [NSNull null],
    @"horizontalAccuracy": valid ? @(location.horizontalAccuracy) : [NSNull null]
  });
#endif
}

+ (id<FBResponsePayload>)handleStopLocationReadback:(FBRouteRequest *)request
{
#if !TARGET_OS_TV && !TARGET_OS_WATCH
  NSString *expected = request.arguments[@"deviceFingerprint"];
  NSString *actual = [UIDevice currentDevice].identifierForVendor.UUIDString;
  if (nil == expected || ![expected isEqualToString:actual]) {
    return FBResponseWithStatus([FBCommandStatus invalidArgumentErrorWithMessage:@"Location reader device mismatch" traceback:nil]);
  }
  [IGLocationManager stopUpdatingLocation];
  IGLocationManager = nil;
  IGLocationDelegate = nil;
#endif
  return FBResponseWithOK();
}
'''

def patch(project):
    path=Path(project)/'WebDriverAgentLib/Commands/FBCustomCommands.m'
    source=path.read_text()
    if 'IG_LOCATION_READBACK_V1' in source:
        start=source.index('+ (id<FBResponsePayload>)handleGetLocation:')
        stop=source.index('+ (id<FBResponsePayload>)handleStopLocationReadback:',start)
        end=source.index('\n+ (id<FBResponsePayload>)',stop+1)
        updated=source[:start]+METHOD+source[end:]
        if updated==source:return False
        path.write_text(updated);return True
    start=source.index('+ (id<FBResponsePayload>)handleGetLocation:')
    end=source.index('\n+ (id<FBResponsePayload>)',start+1)
    source=source[:start]+METHOD+source[end:]
    prefix="""@interface IGLocationReadbackDelegate : NSObject <CLLocationManagerDelegate>
@property (nonatomic, strong) CLLocation *location;
@end
@implementation IGLocationReadbackDelegate
- (void)locationManager:(CLLocationManager *)manager didUpdateLocations:(NSArray<CLLocation *> *)locations { self.location = locations.lastObject; }
- (void)locationManager:(CLLocationManager *)manager didFailWithError:(NSError *)error { self.location = nil; }
@end
static CLLocationManager *IGLocationManager = nil;
static IGLocationReadbackDelegate *IGLocationDelegate = nil;

"""
    source=source.replace('@implementation FBCustomCommands',prefix+'@implementation FBCustomCommands',1)
    route='    [[FBRoute POST:@"/wda/device/location/stop"].withoutSession respondWithTarget:self action:@selector(handleStopLocationReadback:)],\n'
    marker='    [[FBRoute GET:@"/wda/device/location"] respondWithTarget:self action:@selector(handleGetLocation:)],'
    if marker not in source:raise RuntimeError('Unsupported WDA location routes')
    source=source.replace(marker,route+marker,1)
    path.write_text(source);return True
if __name__=='__main__':
    import sys
    for arg in sys.argv[1:]:print(arg,patch(arg))
