#import <UIKit/UIKit.h>
#import <dlfcn.h>
@interface IGLegacyRunnerDelegate : UIResponder <UIApplicationDelegate>
@property(strong,nonatomic) UIWindow *window;
@end
@implementation IGLegacyRunnerDelegate
- (BOOL)application:(UIApplication *)app didFinishLaunchingWithOptions:(NSDictionary *)options {
 self.window=[[UIWindow alloc] initWithFrame:UIScreen.mainScreen.bounds];self.window.rootViewController=[UIViewController new];[self.window makeKeyAndVisible];
 // XCTest serves main-queue requests from a nested run loop. Launch from a timer,
 // not a main-queue dispatch block, so that queue can remain reentrant.
 [self performSelector:@selector(runTests) withObject:nil afterDelay:0];
 return YES;
}
- (void)runTests {
 void *framework=dlopen("/Developer/Library/Frameworks/XCTest.framework/XCTest", RTLD_NOW|RTLD_GLOBAL);
 void (*entry)(id)=framework ? dlsym(framework,"_XCTestMain") : NULL;
 if (!entry) {NSLog(@"LEGACY_XCTEST_LOAD_ERROR %s",dlerror());exit(2);}
 NSLog(@"LEGACY_XCTEST_BEGIN");entry(nil);
}
@end
int main(int argc,char **argv){@autoreleasepool{return UIApplicationMain(argc,argv,nil,NSStringFromClass(IGLegacyRunnerDelegate.class));}}
