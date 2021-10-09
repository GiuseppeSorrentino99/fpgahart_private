import numpy as np
import math
from .base_layer import BaseLayer
np.set_printoptions(precision=5, suppress=True, linewidth=150)

DEBUG=False

class Convolutional3DLayer(BaseLayer):
    def __init__(self, description):
        super().__init__()

        self.input_shape = description['shape_in'][0]
        self.depth_in = self.input_shape[2]
        self.rows_in = self.input_shape[3]
        self.cols_in = self.input_shape[4]
        self.output_shape = description['shape_out']
        self.depth_out = self.output_shape[2]
        self.rows_out = self.output_shape[3]
        self.cols_out = self.output_shape[4]

        self.kernel_shape = description['kernel'][2:]
        self.kd = self.kernel_shape[0]
        self.kh = self.kernel_shape[1]
        self.kw = self.kernel_shape[2]
        self.bias_shape = description['bias']

        self.channels = self.input_shape[1]
        self.filters = self.output_shape[1]

        self.groups = description['groups']

        self.padding = description['padding']
        self.stride = description['stride']
        self.dilation = description['dilation']

        self.branching = description['branching']

        self.depthwise = False
        self.pointwise = False
        if self.groups == self.channels:
            self.depthwise = True
        elif np.prod(np.array(self.kernel_shape)) == 1:
            self.pointwise = True

    def update_layer(self):
        self.channels = self.input_shape[1]

    def get_design_point(self, f_fine, f_coarseIn, f_coarseOut, mem_bw_in, mem_bw_out):
        self.update_layer()

        kernel_elems = int(np.prod(np.array(self.kernel_shape)))

        if self.depthwise:
            self.channels = self.channels//self.groups

        if self.pointwise:
            # Sliding Window Depth
            depth = 1
            # Convolution 3D Depth
            depth += 1
            # Sliding Window buffer in words/elements
            init_buffer = 1
        else:
            # Sliding Window Depth
            depth = self.depth_in * self.rows_in * max(self.kw - 1, 1) * math.ceil(self.channels * f_coarseIn) + math.ceil(self.channels * f_coarseIn) * self.kd * self.kh * max(self.kw - 1, 1)
            # Convolution 3D Depth
            depth += math.ceil(1/f_fine)
            # Sliding Window buffer in words/elements
            init_buffer = self.depth_in * self.rows_in * max(self.kw - 1, 1) * self.channels + self.channels * self.kd * self.kh * max(self.kw - 1, 1)

        max_parallel_muls = kernel_elems * math.ceil(self.channels * f_coarseIn) * math.ceil(self.filters * f_coarseOut)
        max_parallel_adds = (kernel_elems - 1) * math.ceil(self.channels * f_coarseIn) * math.ceil(self.filters * f_coarseOut)
        memory = init_buffer + kernel_elems * self.channels * self.filters

        if not self.depthwise:
            # Accumulation Depth
            depth += (math.ceil(self.channels * f_coarseIn) * math.ceil(self.filters * f_coarseOut))
            # Accumulation Additions
            max_parallel_adds += ((math.ceil(self.channels * f_coarseIn) - 1) * math.ceil(self.filters * f_coarseOut))
            # Accumulation Buffer
            memory += self.channels
            
        gamma_matrix = self.get_rate_matrix(f_fine) * self.get_stream_matrix(f_coarseIn, f_coarseOut) * self.get_data_matrix(mem_bw_in, mem_bw_out)
        if DEBUG:
            print("Γ:\n{}".format(gamma_matrix))
        gamma_matrix_balanced, mem_bounded_in, mem_bounded_out = self.balance_matrix(gamma_matrix.copy())
        if DEBUG:
            print("Γ Balanced:\n{}".format(gamma_matrix_balanced))
        workload_matrix = self.get_workload_matrix()
        ii_matrix = np.nan_to_num(workload_matrix/gamma_matrix_balanced)
        if DEBUG:
            print("II:\n{}".format(ii_matrix))

        latency_sec, latency_cycles, thr_in, thr_out, dsps_util, bram_util = self.get_dp_performance(workload_matrix, ii_matrix, max_parallel_muls, max_parallel_adds, memory, depth)
        total_ops = self.depth_out*self.rows_out*self.cols_out*self.kd*self.kw*self.kh*self.channels*self.filters
        throughput_gops = total_ops/latency_sec
        thr_in /= (np.prod(np.array(self.input_shape[2:])) * self.channels)         # Volumes per second
        thr_out /= (np.prod(np.array(self.output_shape[2:])) * self.filters)        # Volumes per second
        assert math.isclose(thr_in, thr_out), "Thoughputs missmatch. IN = {}, OUT = {}.".format(thr_in, thr_out)

        if DEBUG:
            if dsps_util < 90. and bram_util < 90.:
                print("(fine={:.2f}({}), cIn={:.2f}({}), cOut={:.2f}({}), bwIn={:.2f}, bwOut={:.2f}) DSP % = {:.2f} ({}), BRAM % = {:.2f}, latency = {:.5f}({}), GOPs/s = {:.5f}, In Volumes/s = {:.5f}, Out Volumes/s = {:.5f}, depth = {}, workload(G) = {:.5f}, Mem Bound In={}, Mem Bound Out={}".format(f_fine, math.ceil(f_fine*kernel_elems), f_coarseIn, math.ceil(self.channels * f_coarseIn), f_coarseOut, math.ceil(self.filters * f_coarseOut), mem_bw_in, mem_bw_out, dsps_util, max_parallel_muls, bram_util, latency_sec, int(latency_cycles), throughput_gops*1e-9, thr_in, thr_out, depth, total_ops*1e-9, mem_bounded_in, mem_bounded_out))
            else:
                print("Discarding design point.")

        return f_fine, math.ceil(f_fine*kernel_elems), f_coarseIn, math.ceil(self.channels * f_coarseIn), f_coarseOut, math.ceil(self.filters * f_coarseOut), mem_bw_in, mem_bw_out, dsps_util, max_parallel_muls, bram_util, latency_sec, int(latency_cycles), throughput_gops*1e-9, thr_in, thr_out, depth, total_ops*1e-9, mem_bounded_in, mem_bounded_out

    def get_rate_matrix(self, f_fine):
        if not self.depthwise:
            rate_matrix = np.zeros( shape=(5,6) , dtype=float )
        else:
            rate_matrix = np.zeros( shape=(4,5) , dtype=float )

        # Memory bandwidth splitted between read and write
        rate_matrix[0, 0] = 1
        
        rate_matrix[0, 1] = 1
        rate_matrix[1, 1] = (self.depth_out*self.rows_out*self.cols_out)/(self.depth_in*self.rows_in*self.cols_in)

        rate_matrix[1, 2] = 1
        rate_matrix[2, 2] = 1

        rate_matrix[2, 3] = f_fine
        rate_matrix[3, 3] = f_fine

        if not self.depthwise:
            rate_matrix[3, 4] = 1
            rate_matrix[4, 4] = 1

            # Memory bandwidth splitted between read and write
            rate_matrix[4, 5] = 1
        else:
            # Memory bandwidth splitted between read and write
            rate_matrix[3, 4] = 1

        if DEBUG:
            print("R:\n{}".format(rate_matrix))
        return rate_matrix

    def get_stream_matrix(self, f_coarseIn, f_coarseOut):
        if not self.depthwise:
            stream_matrix = np.zeros( shape=(5,6) , dtype=float )
        else:
            stream_matrix = np.zeros( shape=(4,5) , dtype=float )

        stream_matrix[0, 0] = 1
        
        stream_matrix[0, 1] = math.ceil(self.channels * f_coarseIn)
        stream_matrix[1, 1] = math.ceil(self.channels * f_coarseIn)

        stream_matrix[1, 2] = math.ceil(self.channels * f_coarseIn)
        stream_matrix[2, 2] = math.ceil(self.channels * f_coarseIn) * math.ceil(self.filters * f_coarseOut)

        stream_matrix[2, 3] = math.ceil(self.channels * f_coarseIn) * math.ceil(self.filters * f_coarseOut)
        stream_matrix[3, 3] = math.ceil(self.channels * f_coarseIn) * math.ceil(self.filters * f_coarseOut)

        if not self.depthwise:
            stream_matrix[3, 4] = math.ceil(self.channels * f_coarseIn) * math.ceil(self.filters * f_coarseOut)
            stream_matrix[4, 4] = math.ceil(self.filters * f_coarseOut)

            stream_matrix[4, 5] = 1
        else:
            stream_matrix[3, 4] = 1
        if DEBUG:
            print("S:\n{}".format(stream_matrix))
        return stream_matrix

    def get_data_matrix(self, mem_bw_in, mem_bw_out):
        if not self.depthwise:
            data_matrix = np.zeros( shape=(5,6) , dtype=float )
        else:
            data_matrix = np.zeros( shape=(4,5) , dtype=float )

        data_matrix[0, 0] = mem_bw_in
        
        data_matrix[0, 1] = -1
        data_matrix[1, 1] = self.kd * self.kw * self.kh

        data_matrix[1, 2] = -self.kd * self.kw * self.kh
        data_matrix[2, 2] = self.kd * self.kw * self.kh

        data_matrix[2, 3] = -self.kd * self.kw * self.kh
        data_matrix[3, 3] = 1

        if not self.depthwise:
            data_matrix[3, 4] = -1
            data_matrix[4, 4] = 1

            data_matrix[4, 5] = -mem_bw_out
        else:
            data_matrix[3, 4] = -mem_bw_out

        if DEBUG:
            print("D:\n{}".format(data_matrix))
        return data_matrix

    def get_workload_matrix(self):
        in_volume = self.depth_in * self.rows_in * self.cols_in
        out_volume = self.depth_out * self.rows_out * self.cols_out
        kernel_volume = self.kd * self.kw * self.kh

        if not self.depthwise:
            workload_matrix = np.zeros( shape=(5,6) , dtype=float )
        else:
            workload_matrix = np.zeros( shape=(4,5) , dtype=float )

        workload_matrix[0, 0] = in_volume * self.channels
        
        workload_matrix[0, 1] = in_volume * self.channels
        workload_matrix[1, 1] = out_volume * kernel_volume * self.channels

        workload_matrix[1, 2] = out_volume * kernel_volume * self.channels
        workload_matrix[2, 2] = out_volume * kernel_volume * self.channels * self.filters

        workload_matrix[2, 3] = out_volume * kernel_volume * self.channels * self.filters
        workload_matrix[3, 3] = out_volume * self.channels * self.filters

        if not self.depthwise:
            workload_matrix[3, 4] = out_volume * self.channels * self.filters
            workload_matrix[4, 4] = out_volume * self.filters

            workload_matrix[4, 5] = out_volume * self.filters
        else:
            workload_matrix[3, 4] = out_volume * self.filters     

        if DEBUG:
            print("WL:\n{}".format(workload_matrix))
        return workload_matrix