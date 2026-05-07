use chrono::{DateTime, Datelike};
use serde::{Deserialize, Serialize};
use std::fmt;

/// 月番号（1〜12）を日本語月名文字列に変換する。
///
/// `chrono::Datelike::month()` の戻り値を入力として想定する。
/// 範囲外の値が渡された場合は `unreachable!` でパニックする。
pub fn month_ja(month: u32) -> &'static str {
    match month {
        1 => "1月",
        2 => "2月",
        3 => "3月",
        4 => "4月",
        5 => "5月",
        6 => "6月",
        7 => "7月",
        8 => "8月",
        9 => "9月",
        10 => "10月",
        11 => "11月",
        12 => "12月",
        _ => unreachable!("chrono::month() は 1-12 を保証する。got: {}", month),
    }
}

fn weekday_ja(wd: chrono::Weekday) -> &'static str {
    match wd {
        chrono::Weekday::Mon => "月",
        chrono::Weekday::Tue => "火",
        chrono::Weekday::Wed => "水",
        chrono::Weekday::Thu => "木",
        chrono::Weekday::Fri => "金",
        chrono::Weekday::Sat => "土",
        chrono::Weekday::Sun => "日",
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Default)]
pub enum UserTimezone {
    #[default]
    Utc,
    Local,
}

/// Specifies the *purpose* of a timestamp label when requesting a formatted
/// string from a `UserTimezone` instance.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TimeLabelKind<'a> {
    /// Formatting suitable for axis ticks.  Will choose the appropriate
    /// `HH:MM`, `MM:SS`, or `D` style based on the timeframe.
    Axis { timeframe: exchange::Timeframe },
    /// Formatting for the crosshair tooltip.
    /// Sub-10-second intervals will show `HH:MM:SS.mmm`,
    /// while larger intervals will show `Day Mon D HH:MM`.
    Crosshair { show_millis: bool },
    /// Arbitrary formatting using the given `chrono` specifier string.
    Custom(&'a str),
}

impl UserTimezone {
    pub fn to_user_datetime(
        &self,
        datetime: DateTime<chrono::Utc>,
    ) -> DateTime<chrono::FixedOffset> {
        self.with_user_timezone(datetime, |time_with_zone| time_with_zone)
    }

    /// Formats a Unix timestamp (milliseconds) according to the kind.
    pub fn format_with_kind(&self, timestamp_ms: i64, kind: TimeLabelKind<'_>) -> Option<String> {
        DateTime::from_timestamp_millis(timestamp_ms).map(|datetime| {
            self.with_user_timezone(datetime, |time_with_zone| match kind {
                TimeLabelKind::Axis { timeframe } => {
                    Self::format_by_timeframe(&time_with_zone, timeframe)
                }
                TimeLabelKind::Crosshair { show_millis } => {
                    if show_millis {
                        time_with_zone.format("%H:%M:%S.%3f").to_string()
                    } else {
                        format!(
                            "{}{}日({}) {}",
                            month_ja(time_with_zone.month()),
                            time_with_zone.day(),
                            weekday_ja(time_with_zone.weekday()),
                            time_with_zone.format("%H:%M"),
                        )
                    }
                }
                TimeLabelKind::Custom(fmt) => time_with_zone.format(fmt).to_string(),
            })
        })
    }

    /// Converts a UTC `DateTime` into the user's configured timezone and normalizes it to
    /// `DateTime<FixedOffset>` so downstream formatting can use one concrete type.
    fn with_user_timezone<T>(
        &self,
        datetime: DateTime<chrono::Utc>,
        formatter: impl FnOnce(DateTime<chrono::FixedOffset>) -> T,
    ) -> T {
        let time_with_zone = match self {
            UserTimezone::Local => datetime.with_timezone(&chrono::Local).fixed_offset(),
            UserTimezone::Utc => datetime.fixed_offset(),
        };

        formatter(time_with_zone)
    }

    /// Formats an already timezone-adjusted timestamp for axis labels.
    ///
    /// `timeframe` controls whether output is second-level (`MM:SS`) or minute-level (`HH:MM`).
    /// At exact midnight for non-sub-10s intervals, this returns the day-of-month (`D`) to
    /// emphasize date boundaries on the chart.
    fn format_by_timeframe(
        datetime: &DateTime<chrono::FixedOffset>,
        timeframe: exchange::Timeframe,
    ) -> String {
        let interval = timeframe.to_milliseconds();

        if interval < 10_000 {
            datetime.format("%M:%S").to_string()
        } else if datetime.format("%H:%M").to_string() == "00:00" {
            datetime.format("%-d").to_string()
        } else {
            datetime.format("%H:%M").to_string()
        }
    }
}

impl fmt::Display for UserTimezone {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            UserTimezone::Utc => write!(f, "UTC"),
            UserTimezone::Local => {
                let local_offset = chrono::Local::now().offset().local_minus_utc();
                let hours = local_offset / 3600;
                let minutes = (local_offset % 3600) / 60;
                write!(f, "Local (UTC {hours:+03}:{minutes:02})")
            }
        }
    }
}

impl<'de> Deserialize<'de> for UserTimezone {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        let timezone_str = String::deserialize(deserializer)?;
        match timezone_str.to_lowercase().as_str() {
            "utc" => Ok(UserTimezone::Utc),
            "local" => Ok(UserTimezone::Local),
            _ => Err(serde::de::Error::custom("Invalid UserTimezone")),
        }
    }
}

impl Serialize for UserTimezone {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: serde::Serializer,
    {
        match self {
            UserTimezone::Utc => serializer.serialize_str("UTC"),
            UserTimezone::Local => serializer.serialize_str("Local"),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn month_ja_covers_all_months() {
        for m in 1u32..=12 {
            let s = month_ja(m);
            assert!(!s.contains('?'), "month {m} returned fallback: {s}");
            assert!(s.ends_with('月'), "month {m} should end with 月: {s}");
        }
    }

    #[test]
    #[should_panic(expected = "chrono::month()")]
    fn month_ja_panics_on_zero() {
        month_ja(0);
    }

    #[test]
    #[should_panic(expected = "chrono::month()")]
    fn month_ja_panics_on_thirteen() {
        month_ja(13);
    }

    #[test]
    fn crosshair_format_no_millis_japanese() {
        let tz = UserTimezone::Utc;
        // 2024-01-05 (金) 15:30:00 UTC
        let ts_ms = 1704468600000i64;
        let result = tz.format_with_kind(ts_ms, TimeLabelKind::Crosshair { show_millis: false });
        let s = result.expect("format_with_kind returned None");
        assert!(s.contains("1月"), "should contain 1月, got: {s}");
        assert!(s.contains("金"), "should contain 金, got: {s}");
        assert!(s.contains("15:30"), "should contain 15:30, got: {s}");
    }

    #[test]
    fn crosshair_format_with_millis_unchanged() {
        let tz = UserTimezone::Utc;
        let ts_ms = 1704468600123i64;
        let result = tz.format_with_kind(ts_ms, TimeLabelKind::Crosshair { show_millis: true });
        let s = result.expect("format_with_kind returned None");
        assert!(s.contains("15:30:00"), "got: {s}");
        assert!(s.contains("123"), "got: {s}");
    }
}
