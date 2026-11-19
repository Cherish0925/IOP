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
    /// With a peak: read every line that stands out by at least this much, rather than the one at the extreme.
    prominence: Option<f64>,
    /// Read where the trace crosses this level, rising or falling.
    level: Option<f64>,
    /// Fit a straight line through the trace.
    fit: bool,
    /// Listed coordinates for the selected trace; uniform axes can use positions.
    coordinates: Option<Vec<f64>>,
}

/// Buckets of a view, at most: a map of 2000 by 2000.
const CELLS: usize = 4_000_000;
/// Positions a line is read over, at most; their values are kept while it is found.
const TRACE: usize = 20_000_000;
/// Lines, or crossings of a level, one view reads at most.
const LINES: usize = 1000;

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
    let level = match &request["level"] {
        Value::Null => None,
        value => match value.as_f64() {
            Some(l) if l.is_finite() => Some(l),
            _ => return Err(invalid("level is a finite number")),
        },
    };
    let fit = match request["fit"].as_str() {
        None if request["fit"].is_null() => false,
        Some("line") => true,
        _ => return Err(invalid("fit is line, a straight line through the trace")),
    };
    let traced = peak.is_some() || level.is_some() || fit;
    if traced {
        let one =
            along.len() == 1 && (0..shape.len()).all(|d| d == along[0].axis || fixed[d].is_some());
        if !one {
            return Err(invalid(
                "A peak, a level or a fit is read along one axis, with every other axis fixed",
            ));
        }
        if along[0].stop - along[0].start > TRACE {
            return Err(invalid(format!(
                "A peak, a level or a fit is read over at most {TRACE} positions; select fewer with start and stop"
            )));
        }
    }
    let prominence = match &request["prominence"] {
        Value::Null => None,
        value => match value.as_f64() {
            Some(p) if p.is_finite() && p > 0.0 && peak.is_some() => Some(p),
            _ => {
                return Err(invalid(
                    "prominence is a positive number, read with a peak or a dip",
                ))
            }
        },
    };
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
        if !traced || values.len() != along[0].stop - along[0].start {
            return Err(invalid(
                "coordinates gives one number for each selected position of a peak, a level or a fit",
            ));
        }
        if peak.is_some()
            && !(values.windows(2).all(|p| p[0] < p[1]) || values.windows(2).all(|p| p[0] > p[1]))
        {
            return Err(invalid(
                "A peak needs coordinates that strictly increase or decrease",
            ));
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
        prominence,
        level,
        fit,
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
    let traced = r.peak.is_some() || r.level.is_some() || r.fit;
    let mut trace = Vec::with_capacity(if traced {
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
                if traced {
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
        let coordinates = r.coordinates.as_deref();
        match r.prominence {
            Some(least) => {
                found["peaks"] = lines(&trace, r.along[0].start, peak, coordinates, least)?
            }
            None => found["peak"] = line(&trace, r.along[0].start, peak, coordinates),
        }
    }
    if let Some(level) = r.level {
        found["crossings"] = crossings(&trace, r.along[0].start, level, r.coordinates.as_deref())?;
    }
    if r.fit {
        found["fit"] = line_through(&trace, r.along[0].start, r.coordinates.as_deref());
    }
    Ok(found)
}

/// The straight line through the finite values of a trace that starts at position `first`, by least squares
/// in its positions or listed coordinates, taken from their means: its slope, its value at zero, where it
/// reaches zero, the root mean square of what it leaves and the number of values. Null for fewer than two
/// places.
fn line_through(trace: &[f64], first: usize, coordinates: Option<&[f64]>) -> Value {
    let place = |index: usize| coordinates.map_or((first + index) as f64, |values| values[index]);
    let points: Vec<(f64, f64)> = trace
        .iter()
        .enumerate()
        .filter(|(_, y)| y.is_finite())
        .map(|(i, &y)| (place(i), y))
        .collect();
    let n = points.len() as f64;
    let (sx, sy) = points
        .iter()
        .fold((0.0, 0.0), |(sx, sy), (x, y)| (sx + x, sy + y));
    let (mx, my) = (sx / n, sy / n);
    let (mut sxx, mut sxy) = (0.0, 0.0);
    for (x, y) in &points {
        let dx = x - mx;
        sxx += dx * dx;
        sxy += dx * (y - my);
    }
    if points.len() < 2 || sxx == 0.0 {
        return Value::Null;
    }
    let slope = sxy / sxx;
    let mut squares = 0.0;
    for (x, y) in &points {
        let left = (y - my) - slope * (x - mx);
        squares += left * left;
    }
    let mut found = json!({
        "slope": number(slope),
        "intercept": number(my - slope * mx),
        "zero": if slope != 0.0 { number(mx - my / slope) } else { Value::Null },
        "residual": number((squares / n).sqrt()),
        "count": points.len(),
    });
    if coordinates.is_some() {
        found["coordinates"] = json!(true);
    }
    found
}

/// Where a trace that starts at position `first` crosses `level`, in order: between neighbouring finite
/// values, one below the level and the other at or above it, on the straight line through them, rising when
/// the later is the higher. Values that are not finite are passed over.
fn crossings(
    trace: &[f64],
    first: usize,
    level: f64,
    coordinates: Option<&[f64]>,
) -> Result<Value, Box<dyn Error>> {
    let place = |index: usize| coordinates.map_or((first + index) as f64, |values| values[index]);
    let mut found = Vec::new();
    let mut last: Option<usize> = None;
    for (j, &y) in trace.iter().enumerate() {
        if !y.is_finite() {
            continue;
        }
        if let Some(i) = last {
            let x = trace[i];
            if (x >= level) != (y >= level) {
                if found.len() == LINES {
                    return Err(invalid(format!(
                        "The trace crosses this level more than {LINES} times; select fewer positions"
                    )));
                }
                let mut crossing = json!({
                    "index": first + i,
                    "position": number(place(i) + (level - x) / (y - x) * (place(j) - place(i))),
                    "rising": y > x,
                });
                if coordinates.is_some() {
                    crossing["coordinates"] = json!(true);
                }
                found.push(crossing);
            }
        }
        last = Some(j);
    }
    Ok(Value::Array(found))
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
    match top {
        Some(i) => describe(trace, first, i, base, sign, coordinates),
        None => Value::Null,
    }
}

/// The lines of a trace that stand out by at least `least`: each highest value (lowest for a dip) between
/// lower ones whose prominence, its height above the higher of the lowest values on either side before a
/// higher one or the end of the trace, is that much. That higher lowest value is the line's base, and the
/// line is described as `line` describes the one at the extreme. In order of position.
fn lines(
    trace: &[f64],
    first: usize,
    peak: Peak,
    coordinates: Option<&[f64]>,
    least: f64,
) -> Result<Value, Box<dyn Error>> {
    let sign = if peak == Peak::Max { 1.0 } else { -1.0 };
    let finite: Vec<usize> = (0..trace.len()).filter(|&i| trace[i].is_finite()).collect();
    let z: Vec<f64> = finite.iter().map(|&i| sign * trace[i]).collect();
    // The lowest value from each one back to the nearest higher one, kept for each value on a stack of
    // higher ones: a value that rises above those before it takes in their lowest values.
    let lowest = |order: &mut dyn Iterator<Item = usize>| -> Vec<f64> {
        let mut found = vec![0.0; z.len()];
        let mut higher: Vec<(f64, f64)> = Vec::new();
        for k in order {
            let mut low = z[k];
            while let Some(&(value, below)) = higher.last() {
                if value > z[k] {
                    break;
                }
                low = low.min(below);
                higher.pop();
            }
            found[k] = low;
            higher.push((z[k], low));
        }
        found
    };
    let (left, right) = (lowest(&mut (0..z.len())), lowest(&mut (0..z.len()).rev()));
    let mut found = Vec::new();
    for k in 1..z.len().saturating_sub(1) {
        if z[k - 1] < z[k] && z[k + 1] <= z[k] {
            let base = left[k].max(right[k]);
            if z[k] - base >= least {
                if found.len() == LINES {
                    return Err(invalid(format!(
                        "More than {LINES} lines stand out by this prominence; ask for a larger one"
                    )));
                }
                let mut line = describe(trace, first, finite[k], sign * base, sign, coordinates);
                line["prominence"] = number(z[k] - base);
                found.push(line);
            }
        }
    }
    Ok(Value::Array(found))
}

/// The line at position `i` of a trace over a base: its position refined between its neighbours by the
/// parabola through the three, its value there, and the positions, interpolated, where the trace on either
/// side first comes halfway from that value to the base.
fn describe(
    trace: &[f64],
    first: usize,
    i: usize,
    base: f64,
    sign: f64,
    coordinates: Option<&[f64]>,
) -> Value {
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
    fn the_lines_of_a_trace_stand_out_by_their_prominence() {
        // Lines 1, 0.6 and 0.3 high, 8, 12 and 6 wide, on a ripple whose own crests rise 0.02.
        let shape = |x: f64, c: f64, w: f64| 1.0 / (1.0 + ((x - c) / w).powi(2));
        let values: Vec<f64> = (0..400)
            .map(|i| {
                let x = i as f64;
                shape(x, 80.3, 4.0)
                    + 0.6 * shape(x, 200.7, 6.0)
                    + 0.3 * shape(x, 320.2, 3.0)
                    + 0.01 * (x / 3.0).sin()
            })
            .collect();
        let (_keep, name) = file(&values);
        let found = |peak: &str, least: f64, name: &str| {
            view(
                &parse(&request(
                    name,
                    &[400],
                    json!({"peak": peak, "prominence": least}),
                ))
                .unwrap(),
            )
            .unwrap()["peaks"]
                .as_array()
                .unwrap()
                .clone()
        };
        let peaks = found("max", 0.1, &name);
        assert_eq!(peaks.len(), 3);
        for (line, (center, width)) in peaks.iter().zip([(80.3, 8.0), (200.7, 12.0), (320.2, 6.0)])
        {
            let at = |key: &str| line[key].as_f64().unwrap();
            assert!((at("position") - center).abs() < 0.1, "{line}");
            assert!((at("right") - at("left") - width).abs() < 0.6, "{line}");
        }
        assert!(peaks[1]["prominence"].as_f64().unwrap() > 0.55);
        // The ripple's crests stand out by little; a dip reads the lines of the trace turned over.
        let small = found("max", 0.01, &name);
        let ripple = |line: &&Value| line["prominence"].as_f64().unwrap() < 0.03;
        assert_eq!(
            (small.len() > 3, small.iter().filter(ripple).count()),
            (true, small.len() - 3)
        );
        let turned: Vec<f64> = values.iter().map(|v| -v).collect();
        let (_turned, turned_name) = file(&turned);
        let dips = found("min", 0.1, &turned_name);
        assert_eq!(dips.len(), 3);
        assert_eq!(dips[2]["index"], peaks[2]["index"]);
        assert!(parse(&request(&name, &[400], json!({"prominence": 0.1}))).is_err());
        assert!(parse(&request(
            &name,
            &[400],
            json!({"peak": "max", "prominence": 0})
        ))
        .is_err());
    }

    #[test]
    fn a_trace_crosses_a_level_between_samples() {
        let crossings_of = |values: &[f64], extra: Value| {
            let (_keep, name) = file(values);
            view(&parse(&request(&name, &[values.len()], extra)).unwrap()).unwrap()["crossings"]
                .clone()
        };
        let at = |found: &Value| -> Vec<(f64, bool)> {
            let items = found.as_array().unwrap();
            items
                .iter()
                .map(|c| {
                    (
                        c["position"].as_f64().unwrap(),
                        c["rising"].as_bool().unwrap(),
                    )
                })
                .collect()
        };
        // A sine of period 40, sampled half a position off its zeros, falls through zero at 19.5 and 59.5.
        let sine: Vec<f64> = (0..100)
            .map(|x| (std::f64::consts::TAU * (x as f64 + 0.5) / 40.0).sin())
            .collect();
        let found = at(&crossings_of(&sine, json!({"level": 0.0})));
        let expected = [(19.5, false), (39.5, true), (59.5, false), (79.5, true)];
        assert_eq!(found.len(), 4);
        for ((position, rising), (center, up)) in found.iter().zip(expected) {
            assert!(
                (position - center).abs() < 1e-9 && *rising == up,
                "{found:?}"
            );
        }
        // Positions count from the start of the array; the index is the sample before the crossing.
        let window = crossings_of(&sine, json!({"level": 0.0, "start": 30, "stop": 70}));
        assert_eq!(
            (window[0]["index"].clone(), window.as_array().unwrap().len()),
            (json!(39), 2)
        );
        // A missing value is passed over, and a value at the level counts as reaching it.
        assert_eq!(
            at(&crossings_of(&[-1.0, f64::NAN, 1.0], json!({"level": 0.0}))),
            [(1.0, true)]
        );
        assert_eq!(
            at(&crossings_of(&[0.0, 1.0, 0.0], json!({"level": 1.0}))),
            [(1.0, true), (1.0, false)]
        );
        // Listed coordinates need not increase: a loop swept up and back crosses at a coordinate each way.
        let looped = crossings_of(
            &[-1.0, 1.0, -1.0],
            json!({"level": 0.0, "coordinates": [0.0, 2.0, 1.0]}),
        );
        assert_eq!(at(&looped), [(1.0, true), (1.5, false)]);
        assert_eq!(looped[0]["coordinates"], json!(true));
        let (_keep, name) = file(&sine);
        assert!(parse(&request(&name, &[100], json!({"level": "zero"}))).is_err());
        assert!(parse(&request(&name, &[50, 2], json!({"level": 0.0}))).is_err());
        assert!(parse(&request(
            &name,
            &[3],
            json!({"peak": "max", "coordinates": [0.0, 2.0, 1.0]})
        ))
        .is_err());
    }

    #[test]
    fn a_straight_line_is_fitted_through_a_trace() {
        let fit_of = |values: &[f64], extra: Value| {
            let (_keep, name) = file(values);
            view(&parse(&request(&name, &[values.len()], extra)).unwrap()).unwrap()["fit"].clone()
        };
        let near = |found: &Value, key: &str, expected: f64| {
            assert!(
                (found[key].as_f64().unwrap() - expected).abs() < 1e-12,
                "{key}: {found}"
            );
        };
        // 2 x + 1 with a missing value: it reaches zero at -0.5 and leaves nothing over.
        let line: Vec<f64> = (0..10)
            .map(|x| {
                if x == 4 {
                    f64::NAN
                } else {
                    2.0 * x as f64 + 1.0
                }
            })
            .collect();
        let found = fit_of(&line, json!({"fit": "line"}));
        for (key, expected) in [
            ("slope", 2.0),
            ("intercept", 1.0),
            ("zero", -0.5),
            ("residual", 0.0),
        ] {
            near(&found, key, expected);
        }
        assert_eq!(found["count"], json!(9));
        // A window keeps the positions of the array, and listed coordinates give the line in them.
        near(
            &fit_of(&line, json!({"fit": "line", "start": 5, "stop": 10})),
            "zero",
            -0.5,
        );
        let listed = fit_of(
            &[1.0, 3.0, 5.0],
            json!({"fit": "line", "coordinates": [10.0, 20.0, 30.0]}),
        );
        near(&listed, "slope", 0.2);
        near(&listed, "zero", 5.0);
        assert_eq!(listed["coordinates"], json!(true));
        // Fewer than two places give no line.
        assert_eq!(
            fit_of(&[f64::NAN, 2.0], json!({"fit": "line"})),
            Value::Null
        );
        assert_eq!(
            fit_of(
                &[1.0, 2.0],
                json!({"fit": "line", "coordinates": [3.0, 3.0]})
            ),
            Value::Null
        );
        let (_keep, name) = file(&line);
        assert!(parse(&request(&name, &[10], json!({"fit": "curve"}))).is_err());
    }

    #[test]
    fn inconsistent_requests_are_refused() {
        let (_keep, name) = file(&[1.0, 2.0]);
        assert!(parse(&request(&name, &[2], json!({"stop": 3}))).is_err());
        assert!(parse(&request(&name, &[2], json!({"complex": "abs"}))).is_err());
        assert!(view(&parse(&request(&name, &[3], json!({}))).unwrap()).is_err());
    }
}
