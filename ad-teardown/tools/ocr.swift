// Local OCR with Apple's Vision framework — free, offline, returns per-line confidence + box.
// Built on demand by teardown/ocr.py:  swiftc -O tools/ocr.swift -o data/bin/ocr
// Protocol: image paths on stdin (one per line) -> one JSON object per line on stdout:
//   {"path": "...", "lines": [{"text": "...", "confidence": 0.98, "bbox": [x, y, w, h]}], "error": null}
// bbox is normalized 0..1 with the origin at the TOP-left (Vision's is bottom-left; flipped here).
import Foundation
import Vision

func ocr(_ path: String) -> [String: Any] {
    let url = URL(fileURLWithPath: path)
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true
    request.recognitionLanguages = ["en-US"]
    let handler = VNImageRequestHandler(url: url, options: [:])
    do {
        try handler.perform([request])
    } catch {
        return ["path": path, "lines": [], "error": "\(error)"]
    }
    var lines: [[String: Any]] = []
    for obs in request.results ?? [] {
        guard let top = obs.topCandidates(1).first else { continue }
        let b = obs.boundingBox
        lines.append([
            "text": top.string,
            "confidence": Double(top.confidence),
            "bbox": [b.origin.x, 1 - b.origin.y - b.height, b.width, b.height].map { Double($0) },
        ])
    }
    return ["path": path, "lines": lines, "error": NSNull()]
}

while let line = readLine() {
    let path = line.trimmingCharacters(in: .whitespaces)
    if path.isEmpty { continue }
    let result = ocr(path)
    if let data = try? JSONSerialization.data(withJSONObject: result),
       let s = String(data: data, encoding: .utf8) {
        print(s)
        fflush(stdout)
    }
}
