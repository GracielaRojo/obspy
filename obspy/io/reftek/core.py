import traceback
import warnings
import numpy as np
from obspy import Trace, Stream
from obspy.core.compatibility import from_buffer
from .header import PACKETS_IMPLEMENTED, PAYLOAD
from .util import _bcd_int, _bcd_str, _bcd_hexstr, _parse_short_time


def _read_reftek(filename, network="", component_codes=None, location=""):
    """
    """
    from obspy.io.mseed.util import _unpack_steim_1

    packets = _read_into_packetlist(filename)
    packets = sorted(packets, key=lambda x: x.packet_sequence)
    # for now only support uninterrupted packet sequences
    np.testing.assert_array_equal(
        np.bincount(np.diff([p.packet_sequence for p in packets])),
        [0, len(packets) - 1])
    # drop everything up to first EH packet
    p = packets.pop(0)
    while p.type != "EH":
        p = packets.pop(0)
    eh = p
    header = {
        "network": network,
        "station": (p.station_name + p.station_name_extension).strip(),
        "location": location, "sampling_rate": p.sampling_rate}
    data = {}
    p = packets.pop(0)
    while p.type == "DT":
        if p.data_format != "C0":
            raise NotImplementedError()
        data.setdefault(p.channel_number, []).append(
            (p.time, p.packet_sequence, p.number_of_samples, p.sample_data))
        p = packets.pop(0)
    # expecting an ET packet at the end
    if p.type != "ET":
        raise NotImplementedError()
    st = Stream()
    for channel_number, data_ in data.items():
        # sort by start time of packet
        data_ = sorted(data_)
        # split data into contiguous blocks
        delta = 1.0 / eh.sampling_rate
        data_contiguous = []
        chunk = data_.pop(0)
        chunk_list = [chunk]
        while data_:
            chunk = data_.pop(0)
            t, _, npts, _ = chunk
            if chunk_list:
                t_last, _, npts_last, _ = chunk_list[-1]
                # check if next starttime matches seamless to last chunk
                if t != t_last + npts_last * delta:
                    # gap/overlap, start new contiguous list
                    data_contiguous.append(chunk_list)
                    chunk_list = [chunk]
                    continue
            # otherwise add to current chunk list
            chunk_list.append(chunk)
        data_contiguous.append(chunk_list)
        # read each block into one trace
        for data_ in data_contiguous:
            npts = sum(npts_ for _, _, npts_, _ in data_)
            starttime = data_[0][0]
            data = from_buffer(
                b"".join(dat_[44:] for _, _, _, dat_ in data_), dtype=np.uint8)

            data = _unpack_steim_1(data_string=data,
                                   npts=npts, swapflag=1)

            tr = Trace(data=data, header=header.copy())
            tr.stats.starttime = starttime
            if component_codes is not None:
                tr.stats.channel = (
                    eh.stream_name.strip() + component_codes[channel_number])
            elif p.channel_code is not None:
                tr.stats.channel = eh.channel_code[channel_number]
            else:
                tr.stats.channel = str(channel_number)
            # check if endtime of trace is consistent
            t_last, _, npts_last, _ = data_[-1]
            assert tr.stats.endtime == t_last + (npts_last - 1) * delta
            st += tr

    return st


class Packet(object):
    """
    """
    def __init__(self, type, experiment_number, year, unit_id, time,
                 byte_count, packet_sequence, payload):
        if type not in PACKETS_IMPLEMENTED:
            msg = "Invalid packet type: '{}'".format(type)
            raise ValueError(msg)
        self.type = type
        self.experiment_number = experiment_number
        self.unit_id = unit_id
        self.byte_count = byte_count
        self.packet_sequence = packet_sequence
        self.time = year and _parse_short_time(year, time) or None
        self._parse_payload(payload)

    def __str__(self):
        keys = ("experiment_number", "unit_id", "time", "byte_count",
                "packet_sequence")
        info = ["{}: {}".format(key, getattr(self, key)) for key in keys]
        info.append("-" * 20)
        info += ["{}: {}".format(key, getattr(self, key))
                 for _, _, key, _ in PAYLOAD[self.type]
                 if key != "sample_data"]
        # shorter string for sample data..
        sample_data = getattr(self, "sample_data", None)
        if sample_data is not None:
            np_printoptions = np.get_printoptions()
            np.set_printoptions(threshold=20)
            info.append("sample_data: {}".format(sample_data))
            np.set_printoptions(**np_printoptions)
        return "{} Packet\n\t".format(self.type) + "\n\t".join(info)

    @staticmethod
    def from_string(string):
        """
        """
        if len(string) != 1024:
            msg = "Ignoring incomplete packet."
            warnings.warn(msg)
            return None
        type = string[0:2]
        experiment_number = _bcd_str(string[2:3])
        year = _bcd_int(string[3:4])
        unit_id = _bcd_hexstr(string[4:6])
        time = _bcd_str(string[6:12])
        byte_count = _bcd_int(string[12:14])
        packet_sequence = _bcd_int(string[14:16])
        payload = string[16:]
        return Packet(type, experiment_number, year, unit_id, time, byte_count,
                      packet_sequence, payload)

    def _parse_payload(self, data):
        """
        """
        if self.type not in PAYLOAD:
            msg = ("Not parsing payload of packet type '{}'").format(self.type)
            warnings.warn(msg)
            self._payload = data
            return
        for offset, length, key, converter in PAYLOAD[self.type]:
            value = data[offset:offset+length]
            if converter is not None:
                value = converter(value)
            setattr(self, key, value)


def _parse_next_packet(fh):
    """
    :type fh: file like object
    """
    data = fh.read(1024)
    if not data:
        return None
    if len(data) < 1024:
        msg = "Dropping incomplete packet."
        warnings.warn(msg)
        return None
    try:
        return Packet.from_string(data)
    except:
        msg = "Caught exception parsing packet:\n{}".format(
            traceback.format_exc())
        warnings.warn(msg)
        return None


def _read_into_packetlist(filename):
    """
    """
    with open(filename, "rb") as fh:
        packets = []
        packet = _parse_next_packet(fh)
        while packet:
            if not packet:
                break
            packets.append(packet)
            packet = _parse_next_packet(fh)
    return packets
