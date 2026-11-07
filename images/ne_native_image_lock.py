"""Retired unsafe writer for the inline type-22/slot20 field.

Official Notability's lock/unlock operations append type-23 state events.
The slot20 baseline may remain unchanged after official unlocks; never
overwrite it in order to represent a user's current lock action.

See formats.ne_native.build_bundle(native_image_lock_events=...) instead.
"""
class NativeImageLockError(ValueError):
    pass

def patch_inline_locks(bundle,changes):
    if changes:
        raise NativeImageLockError('禁止覆写 slot20：请改用 type-23 原生事件追加写入')
    return bundle
