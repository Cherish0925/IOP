//! Summarize a saved numerical array without moving its values.
//!
//! One axis is followed, or two for a map or an image; every other axis is either fixed at one index
//! or summarized. The result holds the statistics of the selected elements and, per bucket of
//! consecutive positions along the followed axes, their count, extremes and mean. A last axis of
//! re/im pairs can be read as the modulus, the argument or the power (squared modulus) of the complex
//! values. Along one axis with
//! every other axis fixed, the line at the highest or lowest value is found too: its sub-sample
//! position, its value, the opposite extreme as its base, and where it falls halfway to the base.
//! The bytes of the array are one file or, as for a data stream or the points of a sweep, several
//! files in order; only the range that holds the selected positions of the first axis is read.
use serde_json::{json, Value};
use std::{
    error::Error,
    fs::File,
    io::{self, Read, Seek, SeekFrom},
};

#[derive(Clone, Copy, PartialEq, Debug)]
enum Kind {
    I8,
    U8,
    I16,
    U16,
    I32,
    U32,
    I64,
    U64,
    F32,
    F64,
}

impl Kind {
    fn parse(name: &str) -> Option<Kind> {
        Some(match name {
            "int8" => Kind::I8,
            "uint8" => Kind::U8,
            "int16" => Kind::I16,
            "uint16" => Kind::U16,
            "int32" => Kind::I32,
            "uint32" => Kind::U32,
            "int64" => Kind::I64,
            "uint64" => Kind::U64,
            "float32" => Kind::F32,
            "float64" => Kind::F64,
            _ => return None,
        })
    }

    fn size(self) -> usize {
        match self {
            Kind::I8 | Kind::U8 => 1,
            Kind::I16 | Kind::U16 => 2,
            Kind::I32 | Kind::U32 | Kind::F32 => 4,
            Kind::I64 | Kind::U64 | Kind::F64 => 8,
        }
    }

    fn read(self, b: &[u8], big: bool) -> f64 {
        macro_rules! number {
            ($t:ty, $n:expr) => {{
                let bytes: [u8; $n] = b[..$n].try_into().unwrap();
                (if big {
                    <$t>::from_be_bytes(bytes)
                } else {
                    <$t>::from_le_bytes(bytes)
                }) as f64
            }};
        }
        match self {
            Kind::I8 => b[0] as i8 as f64,
            Kind::U8 => b[0] as f64,
            Kind::I16 => number!(i16, 2),
            Kind::U16 => number!(u16, 2),
            Kind::I32 => number!(i32, 4),
            Kind::U32 => number!(u32, 4),
            Kind::I64 => number!(i64, 8),
            Kind::U64 => number!(u64, 8),
            Kind::F32 => number!(f32, 4),
            Kind::F64 => number!(f64, 8),
        }
    }
}

#[derive(Clone, Copy, PartialEq, Debug)]
enum Complex {
    Modulus,
    Argument,
    Power,
}

/// A line read at the highest value, or at the lowest for a dip.
#[derive(Clone, Copy, PartialEq, Debug)]
enum Peak {
    Max,
    Min,
}

/// An axis followed: the positions selected, start to stop, in a number of buckets.
#[derive(Clone, Copy, Debug)]
struct Followed {
    axis: usize,
    start: usize,
    stop: usize,
    points: usize,
}

#[derive(Debug)]
struct Request {
    /// Files whose bytes, in order, hold the array, with their sizes.
    segments: Vec<(String, u64)>,
    kind: Kind,
    big: bool,
    /// Shape of the selected values: without the re/im axis when complex values are derived.
    shape: Vec<usize>,
    along: Vec<Followed>,
    fixed: Vec<Option<usize>>,
    complex: Option<Complex>,
    peak: Option<Peak>,
    /// Listed coordinates for the selected trace; uniform axes can use positions.
    coordinates: Option<Vec<f64>>,
}

/// Buckets of a view, at most: a map of 2000 by 2000.
const CELLS: usize = 4_000_000;
/// Positions a line is read over, at most; their values are kept while it is found.
const TRACE: usize = 20_000_000;

fn invalid(message: impl Into<String>) -> Box<dyn Error> {
    Box::new(io::Error::new(io::ErrorKind::InvalidInput, message.into()))
}

fn count(value: &Value, what: &str) -> Result<usize, Box<dyn Error>> {
    value
        .as_u64()
        .map(|n| n as usize)
        .ok_or_else(|| invalid(format!("{what} must be a nonnegative integer")))
}

/// One count for each axis followed: a list, or one number for a single axis.
fn counts(value: &Value, what: &str) -> Result<Vec<usize>, Box<dyn Error>> {
    match value.as_array() {
        Some(items) => items.iter().map(|item| count(item, what)).collect(),
        None => Ok(vec![count(value, what)?]),
    }
}

fn parse(request: &Value) -> Result<Request, Box<dyn Error>> {
    let segments = request["segments"]
        .as_array()
        .filter(|parts| !parts.is_empty())
        .ok_or_else(|| invalid("segments lists the files that hold the array, with their sizes"))?
        .iter()
        .map(|part| match (part[0].as_str(), part[1].as_u64()) {
            (Some(path), Some(size)) => Ok((path.to_string(), size)),
            _ => Err(invalid("each segment is [path, size in bytes]")),
        })
        .collect::<Result<Vec<_>, _>>()?;
    let kind = request["dtype"]
        .as_str()
        .and_then(Kind::parse)
        .ok_or_else(|| invalid("dtype is one of int8 … uint64, float32, float64"))?;
    let big = match request["byte_order"].as_str().unwrap_or("little") {
        "little" => false,
        "big" => true,
        _ => return Err(invalid("byte_order is little or big")),
    };
    let mut shape = request["shape"]
        .as_array()
        .ok_or_else(|| invalid("shape is a list of sizes"))?
        .iter()
        .map(|n| count(n, "Each size"))
        .collect::<Result<Vec<_>, _>>()?;
    if shape.is_empty() || shape.contains(&0) {
        return Err(invalid("shape holds positive sizes"));
    }
    let complex = match request["complex"].as_str() {
        None => None,
        Some("abs") => Some(Complex::Modulus),
        Some("arg") => Some(Complex::Argument),
        Some("power") => Some(Complex::Power),
        Some(_) => return Err(invalid("complex is abs, arg or power")),
    };
    if complex.is_some() {
        if shape.len() < 2 || shape[shape.len() - 1] != 2 {
            return Err(invalid("complex values need a last axis of two components"));
        }
        shape.pop();
    }
    let axes = counts(&request["along"], "along")?;
    let starts = counts(&request["start"], "start")?;
    let stops = counts(&request["stop"], "stop")?;
    let points = counts(&request["points"], "points")?;
    if axes.is_empty()
        || [starts.len(), stops.len(), points.len()]
            .iter()
            .any(|n| *n != axes.len())
    {
        return Err(invalid(
            "along, start, stop and points give one value for each axis followed",
        ));
    }
    let mut along = Vec::with_capacity(axes.len());
    for (i, &axis) in axes.iter().enumerate() {
        if axis >= shape.len() || axes[..i].contains(&axis) {
            return Err(invalid("along names different axes of the array"));
        }
        if starts[i] >= stops[i] || stops[i] > shape[axis] {
            return Err(invalid(
                "start and stop select at least one position along each axis followed",
            ));
        }
        if points[i] == 0 {
            return Err(invalid("points must be positive"));
        }
        along.push(Followed {
            axis,
            start: starts[i],
            stop: stops[i],
            points: points[i].min(stops[i] - starts[i]),
        });
    }
    if along.iter().map(|f| f.points).product::<usize>() > CELLS {
        return Err(invalid(format!("A view has at most {CELLS} buckets")));
    }
    let mut fixed = vec![None; shape.len()];
    for pair in request["fixed"]
        .as_array()
        .map(Vec::as_slice)
        .unwrap_or(&[])
    {
        let axis = count(&pair[0], "A fixed axis")?;
        let index = count(&pair[1], "A fixed index")?;
        if axis >= shape.len() || axes.contains(&axis) || index >= shape[axis] {
            return Err(invalid(
                "fixed holds [axis, index] pairs inside the array, other than along",
            ));
        }
        fixed[axis] = Some(index);
    }
    let peak = match request["peak"].as_str() {
        None if request["peak"].is_null() => None,
        Some("max") => Some(Peak::Max),
        Some("min") => Some(Peak::Min),
        _ => return Err(invalid("peak is max, or min for a dip")),
    };
    if peak.is_some() {
        let one =
            along.len() == 1 && (0..shape.len()).all(|d| d == along[0].axis || fixed[d].is_some());
        if !one {
            return Err(invalid(
                "A peak is read along one axis, with every other axis fixed",
            ));
        }
        if along[0].stop - along[0].start > TRACE {
            return Err(invalid(format!(
                "A peak is read over at most {TRACE} positions; select fewer with start and stop"
            )));
        }
    }
    let coordinates = if request["coordinates"].is_null() {
        None
    } else {
        let values: Vec<f64> = request["coordinates"]
            .as_array()
            .ok_or_else(|| invalid("coordinates lists the selected trace's positions"))?
            .iter()
            .map(|v| {
                v.as_f64()
                    .filter(|x| x.is_finite())
                    .ok_or_else(|| invalid("coordinates must be finite numbers"))
            })
            .collect::<Result<_, _>>()?;
        if peak.is_none()
            || values.len() != along[0].stop - along[0].start
            || !(values.windows(2).all(|p| p[0] < p[1]) || values.windows(2).all(|p| p[0] > p[1]))
        {
            return Err(invalid("A peak needs one strictly increasing or decreasing coordinate per selected position"));
        }
        Some(values)
    };
    Ok(Request {
        segments,
        kind,
        big,
        shape,
        along,
        fixed,
        complex,
        peak,
        coordinates,
    })
}

/// The bytes of consecutive files read as one sequence, starting at an offset.
struct Segments<'a> {
    parts: &'a [(String, u64)],
    index: usize,
    file: Option<File>,
    left: u64,
}

impl<'a> Segments<'a> {
    fn at(parts: &'a [(String, u64)], mut offset: u64) -> io::Result<Self> {
        let mut index = 0;
        while index < parts.len() && offset >= parts[index].1 {
            offset -= parts[index].1;
            index += 1;
        }
        let mut segments = Segments {
            parts,
            index,
            file: None,
            left: 0,
        };
        if index < parts.len() {
            let mut file = File::open(&parts[index].0)?;
            file.seek(SeekFrom::Start(offset))?;
            segments.file = Some(file);
            segments.left = parts[index].1 - offset;
        }
        Ok(segments)
    }
}

impl Read for Segments<'_> {
    fn read(&mut self, buffer: &mut [u8]) -> io::Result<usize> {
        while self.left == 0 {
            self.index += 1;
            if self.index >= self.parts.len() {
                return Ok(0);
            }
            self.file = Some(File::open(&self.parts[self.index].0)?);
            self.left = self.parts[self.index].1;
        }
        let wanted = buffer.len().min(self.left as usize);
        let read = self.file.as_mut().unwrap().read(&mut buffer[..wanted])?;
        if read == 0 {
            return Err(io::Error::new(
                io::ErrorKind::UnexpectedEof,
                "a file holds fewer bytes than recorded",
            ));
        }
        self.left -= read as u64;
        Ok(read)
    }
}

#[derive(Clone)]
struct Bucket {
    count: u64,
    finite: u64,
    min: f64,
    max: f64,
    sum: f64,
}

struct Summary {
    count: u64,
    finite: u64,
    mean: f64,
    m2: f64,
    min: f64,
    max: f64,
    min_index: Vec<usize>,
    max_index: Vec<usize>,
}

fn number(value: f64) -> Value {
    // JSON has no NaN or infinity; a statistic without finite values is null.
    if value.is_finite() {
        json!(value)
    } else {
        Value::Null
    }
}

fn view(r: &Request) -> Result<Value, Box<dyn Error>> {
    let size = r.kind.size();
    let unit = size * if r.complex.is_some() { 2 } else { 1 };
    let dims = r.shape.len();
    let mut stride = vec![1usize; dims];
    for d in (0..dims - 1).rev() {
        stride[d] = stride[d + 1] * r.shape[d + 1];
    }
    let total = stride[0] * r.shape[0];
    // Only the slabs of the first axis that hold selected elements are read.
    let (low, high) = match (r.along.iter().find(|f| f.axis == 0), r.fixed[0]) {
        (Some(f), _) => (f.start, f.stop),
        (None, Some(index)) => (index, index + 1),
        (None, None) => (0, r.shape[0]),
    };
    if r.segments.iter().map(|part| part.1).sum::<u64>() < (total * unit) as u64 {
        return Err(invalid(
            "The files hold fewer bytes than the array description",
        ));
    }
    let mut file = Segments::at(&r.segments, (low * stride[0] * unit) as u64)?;
    let mut remaining = (high - low) * stride[0];

    let cells = r.along.iter().map(|f| f.points).product();
    let mut buckets = vec![
        Bucket {
            count: 0,
            finite: 0,
            min: f64::INFINITY,
            max: f64::NEG_INFINITY,
            sum: 0.0
        };
        cells
    ];
    let mut s = Summary {
        count: 0,
        finite: 0,
        mean: 0.0,
        m2: 0.0,
        min: f64::INFINITY,
        max: f64::NEG_INFINITY,
        min_index: Vec::new(),
        max_index: Vec::new(),
    };
    // Along one axis with the others fixed, positions are read in order: the trace a line is found in.
    let mut trace = Vec::with_capacity(if r.peak.is_some() {
        r.along[0].stop - r.along[0].start
    } else {
        0
    });
    let mut index = vec![0usize; dims];
    index[0] = low;
    let per_chunk = (1 << 20) / unit.max(1) + 1;
    let mut buffer = vec![0u8; per_chunk * unit];
    while remaining > 0 {
        let elements = remaining.min(per_chunk);
        let bytes = &mut buffer[..elements * unit];
        file.read_exact(bytes)?;
        for element in bytes.chunks_exact(unit) {
            let selected = r
                .along
                .iter()
                .all(|f| index[f.axis] >= f.start && index[f.axis] < f.stop)
                && r.fixed
                    .iter()
                    .zip(&index)
                    .all(|(f, i)| f.is_none_or(|f| f == *i));
            if selected {
                let value = match r.complex {
                    None => r.kind.read(element, r.big),
                    Some(kind) => {
                        let re = r.kind.read(element, r.big);
                        let im = r.kind.read(&element[size..], r.big);
                        match kind {
                            Complex::Modulus => re.hypot(im),
                            Complex::Argument => im.atan2(re),
                            Complex::Power => re * re + im * im,
                        }
                    }
                };
                // The bucket of each axis followed, in row-major order.
                let cell = r.along.iter().fold(0usize, |cell, f| {
                    cell * f.points
                        + ((index[f.axis] - f.start) as u128 * f.points as u128
                            / (f.stop - f.start) as u128) as usize
                });
                if r.peak.is_some() {
                    trace.push(value);
                }
                let b = &mut buckets[cell];
                b.count += 1;
                s.count += 1;
                if value.is_finite() {
                    b.finite += 1;
                    b.sum += value;
                    b.min = b.min.min(value);
                    b.max = b.max.max(value);
                    s.finite += 1;
                    // Welford's update keeps the variance exact enough for long records.
                    let delta = value - s.mean;
                    s.mean += delta / s.finite as f64;
                    s.m2 += delta * (value - s.mean);
                    if value < s.min {
                        s.min = value;
                        s.min_index = index.clone();
                    }
                    if value > s.max {
                        s.max = value;
                        s.max_index = index.clone();
                    }
                }
            }
            for d in (0..dims).rev() {
                index[d] += 1;
                if index[d] < r.shape[d] || d == 0 {
                    break;
                }
                index[d] = 0;
            }
        }
        remaining -= elements;
    }
    let starts: Vec<Vec<usize>> = r
        .along
        .iter()
        .map(|f| {
            (0..f.points)
                .map(|b| f.start + (b * (f.stop - f.start)).div_ceil(f.points))
                .collect()
        })
        .collect();
    let finite = s.finite > 0;
    let mut found = json!({
        "count": s.count,
        "finite": s.finite,
        "min": if finite { number(s.min) } else { Value::Null },
        "max": if finite { number(s.max) } else { Value::Null },
        "mean": if finite { number(s.mean) } else { Value::Null },
        "std": if finite { number((s.m2 / s.finite as f64).sqrt()) } else { Value::Null },
        "min_index": if finite { json!(s.min_index) } else { Value::Null },
        "max_index": if finite { json!(s.max_index) } else { Value::Null },
        "buckets": {
            "start": starts,
            "count": buckets.iter().map(|b| b.count).collect::<Vec<_>>(),
            "finite": buckets.iter().map(|b| b.finite).collect::<Vec<_>>(),
            "min": buckets.iter().map(|b| if b.finite > 0 { number(b.min) } else { Value::Null }).collect::<Vec<_>>(),
            "max": buckets.iter().map(|b| if b.finite > 0 { number(b.max) } else { Value::Null }).collect::<Vec<_>>(),
            "mean": buckets.iter().map(|b| if b.finite > 0 { number(b.sum / b.finite as f64) } else { Value::Null })
                .collect::<Vec<_>>(),
        }
    });
    if let Some(peak) = r.peak {
        found["peak"] = line(&trace, r.along[0].start, peak, r.coordinates.as_deref());
    }
    Ok(found)
}

/// The line at the extreme of a trace that starts at position `first`: the first highest value (lowest
/// for a dip) and its position, refined between its neighbours by the parabola through the three; the
/// opposite extreme as the base; and the positions, interpolated, where the trace on either side first
/// falls halfway from the line's value to the base. Values that are not finite are passed over.
fn line(trace: &[f64], first: usize, peak: Peak, coordinates: Option<&[f64]>) -> Value {
    let sign = if peak == Peak::Max { 1.0 } else { -1.0 };
    let mut top: Option<usize> = None;
    let mut base = f64::NAN;
    for (i, &y) in trace.iter().enumerate() {
        if y.is_finite() {
            if top.is_none_or(|t| sign * y > sign * trace[t]) {
                top = Some(i);
            }
            if base.is_nan() || sign * y < sign * base {
                base = y;
            }
        }
    }
    let Some(i) = top else {
        return Value::Null;
    };
    let y1 = trace[i];
    let place = |index: usize| coordinates.map_or((first + index) as f64, |values| values[index]);
    let (mut center, mut value) = (place(i), y1);
    if i > 0 && i + 1 < trace.len() && trace[i - 1].is_finite() && trace[i + 1].is_finite() {
        let (y0, y2) = (trace[i - 1], trace[i + 1]);
        // Fit relative to the middle coordinate, including unevenly spaced sweeps.
        let (left, right) = (place(i - 1) - place(i), place(i + 1) - place(i));
        let (slope_left, slope_right) = ((y0 - y1) / left, (y2 - y1) / right);
        let curvature = (slope_right - slope_left) / (right - left);
        let slope = slope_left - curvature * left;
        if sign * curvature < 0.0 {
            let offset = -slope / (2.0 * curvature);
            if offset.is_finite() && offset >= left.min(right) && offset <= left.max(right) {
                center += offset;
                value = y1 + 0.5 * slope * offset;
            }
        }
    }
    let half = (value + base) / 2.0;
    // From the line outward, the first value at or past half height, between it and the last one inside.
    let crossing = |positions: &mut dyn Iterator<Item = usize>| -> Value {
        if sign * (value - base) <= 0.0 {
            return Value::Null;
        }
        let mut inside = i;
        for j in positions {
            let y = trace[j];
            if !y.is_finite() {
                continue;
            }
            if sign * (y - half) > 0.0 {
                inside = j;
                continue;
            }
            let k = inside;
            return number(place(j) + (half - y) / (trace[k] - y) * (place(k) - place(j)));
        }
        Value::Null
    };
    let mut found = json!({
        "index": first + i,
        "position": number(center),
        "value": number(value),
        "base": number(base),
        "left": crossing(&mut (0..i).rev()),
        "right": crossing(&mut (i + 1..trace.len())),
    });
    if coordinates.is_some() {
        found["coordinates"] = json!(true);
    }
    found
}

/// Read one request from standard input and write the view, or an error, as one JSON line.
pub fn run() -> Result<(), Box<dyn Error>> {
    let mut text = String::new();
    io::stdin().read_to_string(&mut text)?;
    let outcome = serde_json::from_str::<Value>(&text)
        .map_err(|e| invalid(format!("The request is not JSON: {e}")))
        .and_then(|request| parse(&request))
        .and_then(|request| view(&request));
    match outcome {
        Ok(value) => println!("{value}"),
        Err(error) => println!("{}", json!({ "error": error.to_string() })),
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    fn file(values: &[f64]) -> (tempfile_path::Path, String) {
        let path = tempfile_path::Path::new();
        let mut f = File::create(&path.0).unwrap();
        for v in values {
            f.write_all(&v.to_le_bytes()).unwrap();
        }
        let name = path.0.to_string_lossy().into_owned();
        (path, name)
    }

    mod tempfile_path {
        pub struct Path(pub std::path::PathBuf);
        impl Path {
            pub fn new() -> Self {
                use std::sync::atomic::{AtomicUsize, Ordering};
                static NEXT: AtomicUsize = AtomicUsize::new(0);
                let n = NEXT.fetch_add(1, Ordering::SeqCst);
                Path(std::env::temp_dir().join(format!("iop-view-{}-{n}.bin", std::process::id())))
            }
        }
        impl Drop for Path {
            fn drop(&mut self) {
                let _ = std::fs::remove_file(&self.0);
            }
        }
    }

    fn request(name: &str, shape: &[usize], extra: Value) -> Value {
        let size = std::fs::metadata(name).unwrap().len();
        let mut value = json!({"segments": [[name, size]], "dtype": "float64", "shape": shape, "along": 0,
                               "start": 0, "stop": shape[0], "points": 4});
        for (k, v) in extra.as_object().unwrap() {
            value[k] = v.clone();
        }
        value
    }

    #[test]
    fn a_trace_is_summarized_in_buckets() {
        let values: Vec<f64> = (0..10).map(|i| i as f64).collect();
        let (_keep, name) = file(&values);
        let v = view(&parse(&request(&name, &[10], json!({}))).unwrap()).unwrap();
        assert_eq!(v["min"], json!(0.0));
        assert_eq!(v["max_index"], json!([9]));
        assert_eq!(v["mean"], json!(4.5));
        assert_eq!(v["buckets"]["start"], json!([[0, 3, 5, 8]]));
        assert_eq!(v["buckets"]["count"], json!([3, 2, 3, 2]));
        assert_eq!(v["buckets"]["max"], json!([2.0, 4.0, 7.0, 9.0]));
    }

    #[test]
    fn complex_pairs_give_modulus_and_a_window_reads_only_its_rows() {
        let values = [3.0, 4.0, 0.0, -2.0, f64::NAN, 1.0, 6.0, 8.0];
        let (_keep, name) = file(&values);
        let v = view(
            &parse(&request(
                &name,
                &[4, 2],
                json!({"complex": "abs", "start": 1, "stop": 4}),
            ))
            .unwrap(),
        )
        .unwrap();
        assert_eq!(
            (v["count"].clone(), v["finite"].clone()),
            (json!(3), json!(2))
        );
        assert_eq!(
            (v["min"].clone(), v["max"].clone()),
            (json!(2.0), json!(10.0))
        );
        assert_eq!(v["buckets"]["min"], json!([2.0, Value::Null, 10.0]));
        let arg = view(
            &parse(&request(
                &name,
                &[4, 2],
                json!({"complex": "arg", "stop": 1}),
            ))
            .unwrap(),
        )
        .unwrap();
        assert!((arg["mean"].as_f64().unwrap() - 4f64.atan2(3.0)).abs() < 1e-12);
        let power =
            view(&parse(&request(&name, &[4, 2], json!({"complex": "power"}))).unwrap()).unwrap();
        assert_eq!(
            power["buckets"]["mean"],
            json!([25.0, 4.0, Value::Null, 100.0])
        );
    }

    #[test]
    fn other_axes_are_fixed_or_summarized() {
        // A 3 x 4 image: rows 0..3, columns 0..4, value = 10 * row + column.
        let values: Vec<f64> = (0..3)
            .flat_map(|r| (0..4).map(move |c| (10 * r + c) as f64))
            .collect();
        let (_keep, name) = file(&values);
        let column = view(
            &parse(&request(
                &name,
                &[3, 4],
                json!({"along": 1, "stop": 4, "fixed": [[0, 2]]}),
            ))
            .unwrap(),
        )
        .unwrap();
        assert_eq!(column["buckets"]["mean"], json!([20.0, 21.0, 22.0, 23.0]));
        let rows = view(&parse(&request(&name, &[3, 4], json!({"points": 8}))).unwrap()).unwrap();
        assert_eq!(rows["buckets"]["min"], json!([0.0, 10.0, 20.0]));
        assert_eq!(rows["buckets"]["max"], json!([3.0, 13.0, 23.0]));
        assert_eq!(rows["max_index"], json!([2, 3]));
    }

    #[test]
    fn two_axes_give_a_map_of_buckets() {
        // A 4 x 6 image, value = 10 * row + column, in 2 x 3 buckets; a window keeps exact values.
        let values: Vec<f64> = (0..4)
            .flat_map(|r| (0..6).map(move |c| (10 * r + c) as f64))
            .collect();
        let (_keep, name) = file(&values);
        let map = view(
            &parse(&request(
                &name,
                &[4, 6],
                json!({"along": [0, 1], "start": [0, 0], "stop": [4, 6], "points": [2, 3]}),
            ))
            .unwrap(),
        )
        .unwrap();
        assert_eq!(map["buckets"]["start"], json!([[0, 2], [0, 2, 4]]));
        assert_eq!(map["buckets"]["count"], json!([4, 4, 4, 4, 4, 4]));
        assert_eq!(
            map["buckets"]["mean"],
            json!([5.5, 7.5, 9.5, 25.5, 27.5, 29.5])
        );
        let window = view(
            &parse(&request(
                &name,
                &[4, 6],
                json!({"along": [1, 0], "start": [4, 1], "stop": [6, 3], "points": [8, 8]}),
            ))
            .unwrap(),
        )
        .unwrap();
        assert_eq!(window["buckets"]["mean"], json!([14.0, 24.0, 15.0, 25.0]));
        assert!(parse(&request(
            &name,
            &[4, 6],
            json!({"along": [0, 0], "start": [0, 0], "stop": [4, 4], "points": [2, 2]})
        ))
        .is_err());
        assert!(parse(&request(
            &name,
            &[4, 6],
            json!({"along": [0, 1], "points": [2, 2]})
        ))
        .is_err());
    }

    #[test]
    fn the_chunks_of_a_stream_read_as_one_array() {
        // Two channels in three chunks that split samples and values anywhere.
        let values: Vec<f64> = (0..12).map(|i| i as f64).collect();
        let (_a, first) = file(&values[..3]);
        let (_b, second) = file(&values[3..8]);
        let (_c, third) = file(&values[8..]);
        let segments = json!([[first, 24], [second, 40], [third, 32]]);
        let request = json!({"segments": segments, "dtype": "float64", "shape": [6, 2], "along": 0,
                             "fixed": [[1, 1]], "start": 1, "stop": 6, "points": 8});
        let v = view(&parse(&request).unwrap()).unwrap();
        assert_eq!(v["buckets"]["mean"], json!([3.0, 5.0, 7.0, 9.0, 11.0]));
    }

    fn peak_of(name: &str, shape: &[usize], extra: Value) -> Value {
        view(&parse(&request(name, shape, extra)).unwrap()).unwrap()["peak"].clone()
    }

    #[test]
    fn a_line_is_found_between_samples_with_its_width_at_half_height() {
        // A Lorentzian of half width 3.2 at 41.3 on a base of 0.5, sampled at each position.
        let values: Vec<f64> = (0..100)
            .map(|x| 0.5 + 1.0 / (1.0 + ((x as f64 - 41.3) / 3.2).powi(2)))
            .collect();
        let (_keep, name) = file(&values);
        let peak = peak_of(&name, &[100], json!({"peak": "max"}));
        assert_eq!(peak["index"], json!(41));
        let at = |key: &str| peak[key].as_f64().unwrap();
        assert!((at("position") - 41.3).abs() < 0.05);
        assert!((at("right") - at("left") - 6.4).abs() < 0.2);
        assert_eq!(peak["base"], json!(values[99]));
        // A dip in a window: positions count from the start of the array.
        let dip: Vec<f64> = values.iter().map(|v| -v).collect();
        let (_dip, name) = file(&dip);
        let found = peak_of(
            &name,
            &[100],
            json!({"peak": "min", "start": 20, "stop": 60}),
        );
        assert_eq!(found["index"], json!(41));
        assert!((found["position"].as_f64().unwrap() - 41.3).abs() < 0.05);
    }

    #[test]
    fn a_line_at_an_edge_or_across_missing_values() {
        let (_keep, name) = file(&[3.0, 2.0, f64::NAN, 1.0, 0.0]);
        let peak = peak_of(&name, &[5], json!({"peak": "max"}));
        // At the first position nothing lies before it; the missing value is passed over.
        assert_eq!(peak["position"], json!(0.0));
        assert_eq!(
            (peak["left"].clone(), peak["right"].clone()),
            (Value::Null, json!(2.0))
        );
        let (_flat, name) = file(&[1.0, 1.0, 1.0]);
        let flat = peak_of(&name, &[3], json!({"peak": "min"}));
        assert_eq!(
            (flat["left"].clone(), flat["right"].clone()),
            (Value::Null, Value::Null)
        );
        let (_image, name) = file(&[0.0; 6]);
        assert!(parse(&request(&name, &[2, 3], json!({"peak": "max"}))).is_err());
        assert!(parse(&request(
            &name,
            &[2, 3],
            json!({"peak": "max", "fixed": [[1, 0]]})
        ))
        .is_ok());
        assert!(parse(&request(
            &name,
            &[2, 3],
            json!({"peak": "top", "fixed": [[1, 0]]})
        ))
        .is_err());
    }

    #[test]
    fn inconsistent_requests_are_refused() {
        let (_keep, name) = file(&[1.0, 2.0]);
        assert!(parse(&request(&name, &[2], json!({"stop": 3}))).is_err());
        assert!(parse(&request(&name, &[2], json!({"complex": "abs"}))).is_err());
        assert!(view(&parse(&request(&name, &[3], json!({}))).unwrap()).is_err());
    }
}
