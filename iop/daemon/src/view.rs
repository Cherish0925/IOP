//! Summarize a saved numerical array without moving its values.
//!
//! One axis is followed; every other axis is either fixed at one index or summarized. The result
//! holds the statistics of the selected elements and, per bucket of consecutive positions along the
//! followed axis, their count, extremes and mean. A last axis of re/im pairs can be read as the
//! modulus or the argument of the complex values. Only the byte range that holds the selected
//! positions of the first axis is read.
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
}

#[derive(Debug)]
struct Request {
    file: String,
    offset: u64,
    kind: Kind,
    big: bool,
    /// Shape of the selected values: without the re/im axis when complex values are derived.
    shape: Vec<usize>,
    along: usize,
    fixed: Vec<Option<usize>>,
    complex: Option<Complex>,
    start: usize,
    stop: usize,
    points: usize,
}

fn invalid(message: impl Into<String>) -> Box<dyn Error> {
    Box::new(io::Error::new(io::ErrorKind::InvalidInput, message.into()))
}

fn count(value: &Value, what: &str) -> Result<usize, Box<dyn Error>> {
    value
        .as_u64()
        .map(|n| n as usize)
        .ok_or_else(|| invalid(format!("{what} must be a nonnegative integer")))
}

fn parse(request: &Value) -> Result<Request, Box<dyn Error>> {
    let file = request["file"]
        .as_str()
        .ok_or_else(|| invalid("file names the array file"))?
        .to_string();
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
        Some(_) => return Err(invalid("complex is abs or arg")),
    };
    if complex.is_some() {
        if shape.len() < 2 || shape[shape.len() - 1] != 2 {
            return Err(invalid("complex values need a last axis of two components"));
        }
        shape.pop();
    }
    let along = count(&request["along"], "along")?;
    if along >= shape.len() {
        return Err(invalid("along names an axis of the array"));
    }
    let mut fixed = vec![None; shape.len()];
    for pair in request["fixed"]
        .as_array()
        .map(Vec::as_slice)
        .unwrap_or(&[])
    {
        let axis = count(&pair[0], "A fixed axis")?;
        let index = count(&pair[1], "A fixed index")?;
        if axis >= shape.len() || axis == along || index >= shape[axis] {
            return Err(invalid(
                "fixed holds [axis, index] pairs inside the array, other than along",
            ));
        }
        fixed[axis] = Some(index);
    }
    let start = count(&request["start"], "start")?;
    let stop = count(&request["stop"], "stop")?;
    if start >= stop || stop > shape[along] {
        return Err(invalid(
            "start and stop select at least one position along the axis",
        ));
    }
    let points = count(&request["points"], "points")?;
    if points == 0 {
        return Err(invalid("points must be positive"));
    }
    Ok(Request {
        file,
        offset: request["offset"].as_u64().unwrap_or(0),
        kind,
        big,
        shape,
        along,
        fixed,
        complex,
        start,
        stop,
        points,
    })
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
    let (low, high) = if r.along == 0 {
        (r.start, r.stop)
    } else if let Some(index) = r.fixed[0] {
        (index, index + 1)
    } else {
        (0, r.shape[0])
    };
    let mut file = File::open(&r.file)?;
    let needed = r.offset + (total * unit) as u64;
    if file.metadata()?.len() < needed {
        return Err(invalid(
            "The file holds fewer bytes than its array description",
        ));
    }
    file.seek(SeekFrom::Start(r.offset + (low * stride[0] * unit) as u64))?;
    let mut remaining = (high - low) * stride[0];

    let n = r.stop - r.start;
    let points = r.points.min(n);
    let mut buckets = vec![
        Bucket {
            count: 0,
            finite: 0,
            min: f64::INFINITY,
            max: f64::NEG_INFINITY,
            sum: 0.0
        };
        points
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
    let mut index = vec![0usize; dims];
    index[0] = low;
    let per_chunk = (1 << 20) / unit.max(1) + 1;
    let mut buffer = vec![0u8; per_chunk * unit];
    while remaining > 0 {
        let elements = remaining.min(per_chunk);
        let bytes = &mut buffer[..elements * unit];
        file.read_exact(bytes)?;
        for element in bytes.chunks_exact(unit) {
            let position = index[r.along];
            let selected = position >= r.start
                && position < r.stop
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
                        if kind == Complex::Modulus {
                            re.hypot(im)
                        } else {
                            im.atan2(re)
                        }
                    }
                };
                let b = &mut buckets
                    [((position - r.start) as u128 * points as u128 / n as u128) as usize];
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
    let starts: Vec<usize> = (0..points)
        .map(|b| r.start + (b * n).div_ceil(points))
        .collect();
    let finite = s.finite > 0;
    Ok(json!({
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
    }))
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
        let mut value = json!({"file": name, "dtype": "float64", "shape": shape, "along": 0,
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
        assert_eq!(v["buckets"]["start"], json!([0, 3, 5, 8]));
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
    fn inconsistent_requests_are_refused() {
        let (_keep, name) = file(&[1.0, 2.0]);
        assert!(parse(&request(&name, &[2], json!({"stop": 3}))).is_err());
        assert!(parse(&request(&name, &[2], json!({"complex": "abs"}))).is_err());
        assert!(view(&parse(&request(&name, &[3], json!({}))).unwrap()).is_err());
    }
}
