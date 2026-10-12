"""Copy browser-recorded video and the actual remote WebRTC audio into one file."""
import heapq
import sys
import av

video_path, audio_path, offset, destination = sys.argv[1:]
with av.open(video_path) as video, av.open(audio_path) as audio, av.open(destination, 'w') as output:
    video_stream = output.add_stream_from_template(video.streams.video[0])
    audio_stream = output.add_stream_from_template(audio.streams.audio[0])

    def packets(source, stream, delay=0):
        for packet in source.demux():
            if packet.dts is None:
                continue
            shift = round(delay / packet.time_base)
            packet.dts += shift
            if packet.pts is not None:
                packet.pts += shift
            timestamp = float(packet.dts * packet.time_base)
            packet.stream = stream
            yield timestamp, packet

    for _, packet in heapq.merge(packets(video, video_stream), packets(audio, audio_stream, float(offset)), key=lambda item: item[0]):
        output.mux(packet)
print(f'Wrote {destination}')
