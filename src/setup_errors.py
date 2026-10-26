"""Classify only the current control setup attempt; never infer trust from USB pairing."""
import re
from pathlib import Path

class SetupError(RuntimeError):
    def __init__(self,code,message,stage='启动验证'):
        super().__init__(message);self.code=code;self.stage=stage


def log_positions(runtime):
    positions={}
    for name in ('appium.log','legacy.log','device-setup.log'):
        try:positions[name]=(Path(runtime)/name).stat().st_size
        except OSError:positions[name]=0
    return positions


def attempt_text(runtime,positions):
    chunks=[]
    for name,start in positions.items():
        try:
            with (Path(runtime)/name).open('rb') as file:
                file.seek(0,2);end=file.tell();file.seek(max(start if end>=start else 0,end-2*1024*1024))
                chunks.append(file.read().decode(errors='replace'))
        except OSError:pass
    return re.sub(r'\x1b\[[0-9;]*m','', '\n'.join(chunks))


def classify_setup_error(error,logs=''):
    if isinstance(error,SetupError):return error
    text=(str(error)+'\n'+logs).casefold()
    if 'invalid trust settings' in text and 'restore system default' in text:
        return SetupError('certificate_trust','Mac 开发证书的信任设置无效。请在钥匙串访问中双击本次 Apple Development 证书，将“使用此证书时”恢复为“使用系统默认值”，保存后重试；不要设为“始终信任”。','签名')
    if 'unable to find a device matching the provided destination' in text:
        return SetupError('xcode_device_missing','Xcode 没有把所选手机列为可用安装目标。请检查 Xcode 的设备支持范围、配对与连接；iOS 16 请使用支持它的 Xcode 版本。无需先重建证书。','设备支持')
    if any(s in text for s in ('unable to verify app','profile needs network validation','requires network validation')):
        return SetupError('network_validation','手机尚未完成 Apple 在线签名验证。请保持手机联网，检查 VPN/代理是否影响 ppq.apple.com 的 HTTPS 连接；在手机打开一次控制组件后重新验证。VPN 与设备管理没有信任条目时，不要反复重签名。')
    if any(s in text for s in ('developer app certificate is not trusted','untrusted developer','开发者未受信任')):
        return SetupError('developer_untrusted','控制组件启动被手机拒绝：开发者 App 尚未受信任或未完成验证。若手机「VPN 与设备管理」有对应开发者条目，请完成信任；若没有条目或提示 Unable to Verify App，请先让手机正常联网并检查 VPN/代理，再打开一次控制组件完成 Apple 在线验证。随后点击「重新验证已安装组件」。无需反复重新签名。')
    if any(s in text for s in ('provisioning profile has expired','certificate has expired','signing certificate is invalid','certificate was revoked')):
        return SetupError('signature_invalid','签名证书或描述文件已失效。请在 Xcode 检查签名账号和有效证书，再重新签名安装。','签名')
    if 'invalid code signature' in text or 'inadequate entitlements' in text:
        return SetupError('signature_or_trust','手机拒绝启动：签名、权限或开发者信任未通过。请先检查手机「VPN 与设备管理」的验证状态；若已验证，再检查本次签名证书和描述文件。')
    if any(s in text for s in ('maximum number of apps for free development','maximum app id limit','maximum number of registered app ids')):
        return SetupError('account_limit','苹果开发账号的 App 安装或标识注册额度已达上限。请在 Xcode 查看账号限制和已有开发 App；反复安装或创建证书不会解除额度限制。','签名')
    if 'no account for team' in text:
        return SetupError('signing_account_missing','所选签名团队在这台 Mac 的 Xcode 中没有可用账号。钥匙串中有旧证书不代表可为新设备签名；请选择本机已登录且可用的团队，或在 Xcode → Settings → Accounts 登录该团队后重试。无需修改手机开发者模式。','签名')
    if any(s in text for s in ('no signing certificate','no profiles for','requires a provisioning profile','signing for','no accounts','authentication credentials','failed to register bundle identifier')):
        return SetupError('signing_failed','Xcode 未能完成开发签名或描述文件配置。请在 Xcode → Settings → Accounts 检查本次团队的账号、证书和设备注册；详细原因见本次 device-setup.log / appium.log。','签名')
    if 'unable to verify app' in text:
        return SetupError('verification','手机尚未完成 App 验证。请保持手机联网，在 VPN 与设备管理中查看开发者 App 的验证提示，再重新验证控制组件。')
    if any(s in text for s in ('device is locked','device locked','unlock the device','password protected','设备已锁定')):
        return SetupError('locked','手机处于锁定状态。请输入锁屏密码解锁并保持亮屏，再重新验证控制组件。')
    if any(s in text for s in ('developer mode is disabled','developer mode disabled','developer mode must','开发者模式')):
        return SetupError('developer_mode','请在手机「设置 → 隐私与安全性 → 开发者模式」开启后重启，并在手机上确认开启。','设备准备')
    if 'timed out while enabling automation mode' in text:
        return SetupError('automation_timeout','手机启用 UI 自动化超时。请保持解锁并检查手机弹窗；在「设置 → 开发者」中如有「UI 自动化 / Enable UI Automation」，请开启，再重新验证。反复超时时重启手机后重新连接，无需先重建证书。')
    if any(s in text for s in ('could not locate device support','failed to mount developer disk','developer disk image is not mounted','unsupported os version')):
        return SetupError('device_support','Xcode 的设备支持环境未就绪或与手机系统不匹配。请在 Xcode → Window → Devices and Simulators 查看该手机并完成支持组件准备，再重新验证。','设备支持')
    if any(s in text for s in ('** test build failed **','** build failed **','fatal error:','compilation failed')):
        return SetupError('build_failed','控制组件编译失败，尚未完成安装。请查看本次 device-setup.log / appium.log 的首个编译错误；此时手机可能没有对应开发者信任项。','编译')
    if any(s in text for s in ('unknown device or simulator udid','device is not connected','device disconnected','no device found','device not found','usbmux connection failed','econnreset','socket hang up','connection refused','连接中断','usb')):
        return SetupError('connection','手机或控制服务通信未就绪。请检查这台手机的 USB 数据连接、解锁和「信任此电脑」；重新检测后再验证。','连接')
    return SetupError('unknown','控制组件配置未完成，暂不能开始浏览。请打开诊断日志查看本次安装/启动错误；未检测到明确原因，不会自动重复安装。')
