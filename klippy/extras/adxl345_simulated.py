# Generate acceleration data by simulating adxl345 chip
#
# Copyright (C) 2020  Dmitry Butyugin <dmbutyugin@google.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging, time, multiprocessing, os
from . import adxl345
from adxl345 import Accel_Measurement

# Results of the simulated measurements
class ADXL345SimulatedResults:
    def __init__(self):
        self.accel_bounds = []
        self.samples = []
        self.time_per_sample = self.start_range = self.end_range = 0.
    def setup_data(self, query_rate, accel_bounds, reactor):
        self.reactor = reactor
        self.accel_bounds = accel_bounds
        self.start_range = time = self.accel_bounds[0].time
        self.end_range = end_range = self.accel_bounds[-1].time
        self.time_per_sample = 1. / query_rate
    def get_stats(self):
        return ("time_per_sample=%.9f,start_range=%.6f,end_range=%.6f"
                % (self.time_per_sample, self.start_range, self.end_range))
    def _gen_samples(self):
        accel_bounds = self.accel_bounds
        if not accel_bounds:
            return
        time, end_range = accel_bounds[0].time, accel_bounds[-1].time
        time_per_sample = self.time_per_sample
        i = 0
        while time < end_range:
            while time >= accel_bounds[i+1].time:
                i += 1
            _, accel_x, accel_y, accel_z = accel_bounds[i]
            yield Accel_Measurement(time, accel_x, accel_y, accel_z)
            time += time_per_sample
    def decode_samples(self):
        samples = self.samples
        if not self.accel_bounds:
            return samples
        self.start_range = self.accel_bounds[0].time
        self.end_range = self.accel_bounds[-1].time
        for sample in self._gen_samples():
            samples.append(sample)
        return samples
    def write_to_file(self, filename):
        def write_impl():
            try:
                # Try to re-nice writing process
                os.nice(20)
            except:
                pass
            f = open(filename, "w")
            f.write("##%s\n#time,accel_x,accel_y,accel_z\n" % (
                self.get_stats(),))
            for s in self._gen_samples():
                f.write("%.6f,%.6f,%.6f,%.6f\n" % (
                    s.time, s.accel_x, s.accel_y, s.accel_z))
            f.close()
        write_proc = multiprocessing.Process(target=write_impl)
        write_proc.daemon = True
        write_proc.start()
        eventtime = last_report_time = self.reactor.monotonic()
        while write_proc.is_alive():
            if eventtime > last_report_time + 5.:
                last_report_time = eventtime
                gcode.respond_info("Wait for writing..", log=False)
            eventtime = self.reactor.pause(eventtime + .1)

# Printer class that controls measurments
class ADXL345Simulated:
    def __init__(self, config, printer=None):
        self.printer = config.get_printer() if printer is None else printer
        self.query_rate = 0
        self.accel_bounds = []
        self.name = "simulated"
        if config:
            self.data_rate = config.getint('rate', 3200, minval=1)
            if len(config.get_name().split()) > 1:
                self.name = config.get_name().split()[1]
            # Register commands
            gcode = self.printer.lookup_object('gcode')
            gcode.register_mux_command("ACCELEROMETER_MEASURE", "CHIP",
                                       self.name,
                                       self.cmd_ACCELEROMETER_MEASURE,
                                       desc=self.cmd_ACCELEROMETER_MEASURE_help)
        else:
            self.data_rate = 3200
    def _handle_move(self, move_time, move):
        if not move.is_kinematic_move:
            return
        if move.accel_t > 0.:
            x = move.accel * move.axes_r[0]
            y = move.accel * move.axes_r[1]
            z = move.accel * move.axes_r[2]
            self.accel_bounds.append(Accel_Measurement(move_time, x, y, z))
        move_time += move.accel_t
        if move.cruise_t > 0.:
            self.accel_bounds.append(Accel_Measurement(move_time, 0., 0., 0.))
        move_time += move.cruise_t
        if move.decel_t > 0.:
            x = -move.accel * move.axes_r[0]
            y = -move.accel * move.axes_r[1]
            z = -move.accel * move.axes_r[2]
            self.accel_bounds.append(Accel_Measurement(move_time, x, y, z))
        move_time += move.decel_t
        self.accel_bounds.append(Accel_Measurement(move_time, 0., 0., 0.))
    def start_measurements(self, rate=None):
        rate = rate or self.data_rate
        self.accel_bounds = []
        # Setup samples
        toolhead = self.printer.lookup_object('toolhead')
        print_time = toolhead.get_last_move_time()
        # Start move tracking
        toolhead.register_move_monitoring_callback(self._handle_move)
        self.query_rate = rate
    def finish_measurements(self):
        query_rate = self.query_rate
        if not query_rate:
            return ADXL345SimulatedResults()
        # Halt move tracking
        toolhead = self.printer.lookup_object('toolhead')
        print_time = toolhead.get_last_move_time()
        toolhead.unregister_move_monitoring_callback(self._handle_move)
        accel_bounds = self.accel_bounds
        self.accel_bounds = []
        res = ADXL345SimulatedResults()
        res.setup_data(query_rate, accel_bounds, self.printer.reactor)
        logging.info("Simulated ADXL345 finished measurements: %s",
                     res.get_stats())
        return res
    def end_query(self, name):
        if not self.query_rate:
            return
        res = self.finish_measurements()
        # Write data to file
        filename = "/tmp/adxl345-%s.csv" % (name,)
        res.write_to_file(filename)
    cmd_ACCELEROMETER_MEASURE_help = "Start/stop simulated accelerometer"
    def cmd_ACCELEROMETER_MEASURE(self, gcmd):
        if self.query_rate:
            name = gcmd.get("NAME", time.strftime("%Y%m%d_%H%M%S"))
            if not name.replace('-', '').replace('_', '').isalnum():
                raise gcmd.error("Invalid adxl345 NAME parameter")
            self.end_query(name)
            gcmd.respond_info("adxl345 measurements stopped")
        else:
            rate = gcmd.get_int("RATE", self.data_rate)
            if rate not in QUERY_RATES:
                raise gcmd.error("Not a valid adxl345 query rate: %d" % (rate,))
            self.start_measurements(rate)
            gcmd.respond_info("adxl345 measurements started")

def load_config(config):
    return ADXL345Simulated(config)
def load_config_prefix(config):
    return ADXL345Simulated(config)
