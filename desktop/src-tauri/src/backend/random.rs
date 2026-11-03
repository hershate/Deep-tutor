use super::error::{BackendError, Result};

#[cfg(windows)]
fn fill_random(bytes: &mut [u8]) -> Result<()> {
    #[link(name = "bcrypt")]
    unsafe extern "system" {
        fn BCryptGenRandom(
            algorithm: *mut std::ffi::c_void,
            buffer: *mut u8,
            length: u32,
            flags: u32,
        ) -> i32;
    }
    let status = unsafe {
        BCryptGenRandom(
            std::ptr::null_mut(),
            bytes.as_mut_ptr(),
            bytes.len() as u32,
            2,
        )
    };
    if status >= 0 {
        Ok(())
    } else {
        Err(BackendError::new(
            "secure_random_unavailable",
            "系统无法生成安全随机令牌，未执行创建操作",
        ))
    }
}

#[cfg(unix)]
fn fill_random(bytes: &mut [u8]) -> Result<()> {
    use std::io::Read;
    std::fs::File::open("/dev/urandom")?
        .read_exact(bytes)
        .map_err(|_| {
            BackendError::new(
                "secure_random_unavailable",
                "系统无法生成安全随机令牌，未执行创建操作",
            )
        })
}

pub(crate) fn encode(bytes: &[u8]) -> String {
    const ALPHABET: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    let mut output = String::new();
    for chunk in bytes.chunks(3) {
        let value = (u32::from(chunk[0]) << 16)
            | (u32::from(*chunk.get(1).unwrap_or(&0)) << 8)
            | u32::from(*chunk.get(2).unwrap_or(&0));
        output.push(ALPHABET[(value >> 18) as usize & 63] as char);
        output.push(ALPHABET[(value >> 12) as usize & 63] as char);
        if chunk.len() >= 2 {
            output.push(ALPHABET[(value >> 6) as usize & 63] as char);
        }
        if chunk.len() == 3 {
            output.push(ALPHABET[value as usize & 63] as char);
        }
    }
    output
}

pub(crate) fn token(prefix: &str) -> Result<String> {
    let mut bytes = [0_u8; 32];
    fill_random(&mut bytes)?;
    Ok(format!("{prefix}{}", encode(&bytes)))
}
