//! RFC 3339 UTC timestamps without a date library.

use std::time::{SystemTime, UNIX_EPOCH};

/// The current time as `YYYY-MM-DDTHH:MM:SSZ`.
pub fn now_rfc3339() -> String {
    format_unix(now_unix())
}

/// Seconds since the Unix epoch.
pub fn now_unix() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// Parse a protocol timestamp (`YYYY-MM-DDTHH:MM:SS[.fraction]Z`) into whole Unix seconds.
pub fn parse_rfc3339(text: &str) -> Option<u64> {
    let bytes = text.as_bytes();
    // ASCII first: every slice below is by byte offset, and one inside a character panics.
    if bytes.len() < 20 || !text.is_ascii() || !text.ends_with('Z') {
        return None;
    }
    let shape_ok = [4, 7].iter().all(|&i| bytes[i] == b'-')
        && bytes[10] == b'T'
        && [13, 16].iter().all(|&i| bytes[i] == b':');
    let fraction = &text[19..text.len() - 1];
    let fraction_ok = fraction.is_empty()
        || (fraction.len() >= 2
            && fraction.starts_with('.')
            && fraction[1..].bytes().all(|b| b.is_ascii_digit()));
    if !shape_ok || !fraction_ok {
        return None;
    }
    let number = |range: std::ops::Range<usize>| -> Option<u64> {
        let part = &text[range];
        part.bytes()
            .all(|b| b.is_ascii_digit())
            .then(|| part.parse().ok())?
    };
    let (year, month, day) = (number(0..4)?, number(5..7)?, number(8..10)?);
    let (hour, minute, second) = (number(11..13)?, number(14..16)?, number(17..19)?);
    if !(1..=12).contains(&month)
        || !(1..=31).contains(&day)
        || hour > 23
        || minute > 59
        || second > 60
    {
        return None;
    }
    let days = days_from_civil(year as i64, month as u32, day as u32);
    u64::try_from(days)
        .ok()
        .map(|d| d * 86_400 + hour * 3600 + minute * 60 + second)
}

// Howard Hinnant's civil-to-days algorithm.
fn days_from_civil(year: i64, month: u32, day: u32) -> i64 {
    let y = if month <= 2 { year - 1 } else { year };
    let era = y.div_euclid(400);
    let yoe = y.rem_euclid(400);
    let mp = i64::from((month + 9) % 12);
    let doy = (153 * mp + 2) / 5 + i64::from(day) - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

/// Format seconds since the Unix epoch as an RFC 3339 UTC timestamp.
pub fn format_unix(secs: u64) -> String {
    let days = (secs / 86_400) as i64;
    let rem = secs % 86_400;
    let (year, month, day) = civil_from_days(days);
    format!(
        "{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}Z",
        rem / 3600,
        (rem % 3600) / 60,
        rem % 60
    )
}

// Howard Hinnant's days-to-civil algorithm.
fn civil_from_days(days: i64) -> (i64, u32, u32) {
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let month = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    let year = yoe + era * 400 + i64::from(month <= 2);
    (year, month, day)
}

#[cfg(test)]
mod tests {
    use super::{format_unix, parse_rfc3339};

    #[test]
    fn formats_known_instants() {
        assert_eq!(format_unix(0), "1970-01-01T00:00:00Z");
        assert_eq!(format_unix(951_782_400), "2000-02-29T00:00:00Z");
        assert_eq!(format_unix(1_793_268_245), "2026-10-29T10:04:05Z");
    }

    #[test]
    fn parses_what_it_formats_and_drops_fractions() {
        for secs in [0, 951_782_400, 1_793_268_245, 4_102_444_799] {
            assert_eq!(parse_rfc3339(&format_unix(secs)), Some(secs));
        }
        assert_eq!(
            parse_rfc3339("2026-10-29T10:04:05.123456789Z"),
            Some(1_793_268_245)
        );
        for bad in [
            "",
            "2026-10-29 10:04:05Z",
            "2026-10-29T10:04:05",
            "2026-13-29T10:04:05Z",
            "2026-10-29T10:04:05.Z",
            "+026-10-29T10:04:05Z",
            "2026-10-29T10:04:0\u{e9}Z",
            "2026-10-29T10:04:05.1\u{e9}Z",
        ] {
            assert_eq!(parse_rfc3339(bad), None, "{bad}");
        }
    }
}
