# Production validation

The workflow has been exercised on a short pilot and a complete *Voltes V* episode 1. Episode assets remain local; this repository publishes tooling and instructions.

| Property | Pilot | Full episode |
| --- | --- | --- |
| Duration | 2:27 | 24:10 |
| Directed performances | 31 | 165 |
| Output picture | 640×480, H.264 | 640×480, H.264 |
| Audio | One Indonesian AAC stereo track | One Indonesian AAC stereo track |
| Main export | About 9.4 MB | 116.3 MB |
| Smaller copy | — | 77.3 MB, about 34% smaller |
| Maximum measured fitting speedup | About 1.081× | About 1.116× |

The full production reused the 31 unchanged pilot voice specifications. Seventeen longer performances were condensed and rerecorded rather than exceeding the 1.12× fitting cap. Opening, combination reprise and ending songs retained their original singing within the single audio stream. The source's burned captions and watermark remained.

The source audio extended slightly beyond its last picture frame. A separate lossless working copy held the last frame while retaining all original decoded pictures and audio packets. Full-episode checks established picture and audio coverage, exact frame count, format, fast start, source identity and complete decoding.

Listening to the separated stems exposed attack calls in the music estimate. Source-linked cleanup, a short evidenced instrumental repair and a complete source-level reference addressed those cases. These operations are now available through the optional refinement stage; they remain editorial decisions based on actual stem listening.

Eight bounded samples of the rendered full episode, totaling about 322 seconds, received independent Indonesian ASR and model listening checks. Actual exported frames were inspected as well. These are sampled quality checks; pronunciation, acting and separation still need listening for each production. Mechanical checks alone do not assess translation quality.

Offline regression fixtures use generated pictures and tones to exercise request reuse, uncertain-job guards, source corrections, frame/sample synchronization, event treatment, song passthrough, source-tail conditioning, refinement boundaries and smaller-export integrity. The optional pinned cleanup adapter has also been exercised on CUDA with generated fixtures. CI runs the offline suite on Windows and Linux without provider keys or model downloads.

Simultaneous performance lanes and precise lip synchronization remain unsupported. See the [production workflow](workflow.md), [postproduction commands](postproduction.md), [performance policy](performance.md) and earlier [pilot measurements](pilot.md).
