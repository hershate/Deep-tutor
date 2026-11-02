use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;
use std::time::Duration;

pub(crate) struct TestServer {
    address: String,
    requests: Arc<Mutex<Vec<String>>>,
    stop: Arc<AtomicBool>,
    worker: Option<JoinHandle<()>>,
}

impl TestServer {
    pub(crate) fn url(&self) -> String {
        format!("http://{}", self.address)
    }

    pub(crate) fn request_count(&self) -> usize {
        self.requests.lock().unwrap().len()
    }
}

impl Drop for TestServer {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
        let _ = TcpStream::connect(&self.address);
        if let Some(worker) = self.worker.take() {
            worker.join().unwrap();
        }
    }
}

pub(crate) fn server<F>(handler: F) -> TestServer
where
    F: Fn(&str, &mut TcpStream) + Send + Sync + 'static,
{
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let address = listener.local_addr().unwrap().to_string();
    let requests = Arc::new(Mutex::new(Vec::new()));
    let stop = Arc::new(AtomicBool::new(false));
    let worker_requests = Arc::clone(&requests);
    let worker_stop = Arc::clone(&stop);
    let worker = std::thread::spawn(move || {
        for incoming in listener.incoming() {
            if worker_stop.load(Ordering::Acquire) {
                break;
            }
            let Ok(mut stream) = incoming else { break };
            stream
                .set_read_timeout(Some(Duration::from_secs(2)))
                .unwrap();
            let Some(request) = read_request(&mut stream) else {
                if worker_stop.load(Ordering::Acquire) {
                    break;
                }
                continue;
            };
            worker_requests.lock().unwrap().push(request.clone());
            handler(&request, &mut stream);
        }
    });
    TestServer {
        address,
        requests,
        stop,
        worker: Some(worker),
    }
}

fn read_request(stream: &mut TcpStream) -> Option<String> {
    let mut request = Vec::new();
    let mut buffer = [0u8; 4096];
    let mut expected_length = None;
    loop {
        let count = stream.read(&mut buffer).ok()?;
        if count == 0 {
            return None;
        }
        request.extend_from_slice(&buffer[..count]);
        if request.len() > 1024 * 1024 {
            return None;
        }
        if expected_length.is_none() {
            let header_end = request.windows(4).position(|bytes| bytes == b"\r\n\r\n")?;
            let headers = String::from_utf8_lossy(&request[..header_end]);
            let content_length = headers
                .lines()
                .find_map(|line| {
                    let (name, value) = line.split_once(':')?;
                    name.eq_ignore_ascii_case("content-length")
                        .then(|| value.trim().parse::<usize>().ok())?
                })
                .unwrap_or(0);
            expected_length = Some(header_end + 4 + content_length);
        }
        if request.len() >= expected_length.unwrap() {
            return Some(String::from_utf8_lossy(&request).into_owned());
        }
    }
}

pub(crate) fn response(stream: &mut TcpStream, status: &str, body: &str) {
    let response = format!(
        "HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
        body.len()
    );
    let _ = stream.write_all(response.as_bytes());
}

pub(crate) fn start_stream(stream: &mut TcpStream) {
    let _ = stream.write_all(
        b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n",
    );
    let _ = stream.flush();
}
