#!/usr/bin/env python

import pysdr # our python package
import numpy as np
import time 
from scipy.signal import firwin # FIR filter design using the window method
from bokeh.layouts import column, row, gridplot, Spacer, widgetbox
from bokeh.models import Select, TextInput
from multiprocessing import Process, Manager 

# USRP Parameters
center_freq = 101.1e6
samp_rate = 1e6
gain = 50

# Other Parameters
fft_size = 512               # output size of fft, the input size is the samples_per_batch
waterfall_samples = 100      # number of rows of the waterfall
samples_in_time_plots = 500  # should be less than samples per batch (2044 for B200)

# Set up the shared buffer between DSP and GUI threads (using multiprocessing's Manager).  it must be global
manager = Manager()
shared_buffer = manager.dict() # there is also an option to use a list, but throwing everything in a dict seems nice

# THIS IS WHERE ALL THE "BLOCKS" GET CREATED
# create a streaming-type FIR filter (this should act the same as a FIR filter block in GNU Radio)
taps = firwin(numtaps=100, cutoff=200e3, nyq=samp_rate) # scipy's filter designer
prefilter = pysdr.fir_filter(taps)

# Function that processes each batch of samples that comes in (currently, all DSP goes here)
def process_samples(samples):
    startTime = time.time()
    #samples = prefilter.filter(samples) # uncomment this to add a filter
    PSD = 10.0 * np.log10(np.abs(np.fft.fftshift(np.fft.fft(samples, fft_size)/float(fft_size)))**2) # calcs PSD
    # add row to waterfall
    waterfall = shared_buffer['waterfall'] # pull waterfall from buffer
    waterfall[:] = np.roll(waterfall, -1, axis=0) # shifts waterfall 1 row
    waterfall[-1,:] = PSD # fill last row with new fft results
    # stick everything we want to display into the shared buffer
    shared_buffer['waterfall'] = waterfall # remember to copy it back into the buffer
    shared_buffer['psd'] = PSD # overwrites whatever was in psd buffer, so that the GUI uses the most recent one when it goes to refresh itself
    shared_buffer['i'] = np.real(samples[0:samples_in_time_plots]) # i buffer
    shared_buffer['q'] = np.imag(samples[0:samples_in_time_plots]) # q buffer
    shared_buffer['utilization'] = (time.time() - startTime)/float(len(samples))*samp_rate # should be below 1.0 to avoid overflows
    
    
# This is where we read in samples from the USRP
def run_usrp():
    # Initialize USRP
    usrp = pysdr.usrp_source('') # this is where you would choose which addr or usrp type
    usrp.set_samp_rate(samp_rate) 
    usrp.set_center_freq(center_freq)
    usrp.set_gain(gain)
    usrp.prepare_to_rx()
    shared_buffer['usrp-signal'] = (False, '') # temporary way of signaling commands to the usrp, very hacky
    while True:
        if shared_buffer['usrp-signal'][0] == True:  # these 3 lines are how the GUI tells the USRP to change gain and freq, it needs rework
            eval('usrp.' + shared_buffer['usrp-signal'][1])
            shared_buffer['usrp-signal'] = (False, '')
        samples = usrp.recv() # receive samples! pretty sure this function is blocking
        process_samples(samples) # send samples to DSP
        
# We do run_usrp() and process_samples() in a 2nd thread, while the Bokeh GUI stuff is in the main thread
usrp_dsp_process = Process(target=run_usrp) 
usrp_dsp_process.start()

# This is the Bokeh "document", where the GUI and controls are set up
def main_doc(doc):
    # Frequncy Sink (line plot)
    fft_plot = pysdr.base_plot('Freq [MHz]', 'PSD [dB]', 'Frequency Sink', disable_horizontal_zooming=True) 
    f = (np.linspace(-samp_rate/2.0, samp_rate/2.0, fft_size) + center_freq)/1e6
    shared_buffer['psd'] = np.zeros(fft_size) # this buffer is how the DSP sends data to the plot in realtime
    fft_line = fft_plot.line(f, np.zeros(len(f)), color="aqua", line_width=1) # set x values but use dummy values for y
    
    # Time Sink (line plot)
    time_plot = pysdr.base_plot('Time [ms]', ' ', 'Time Sink', disable_horizontal_zooming=True) 
    t = np.linspace(0.0, samples_in_time_plots / samp_rate, samples_in_time_plots) * 1e3 # in ms
    shared_buffer['i'] = np.zeros(samples_in_time_plots) # I buffer (time domain)
    timeI_line = time_plot.line(t, np.zeros(len(t)), color="aqua", line_width=1) # set x values but use dummy values for y
    shared_buffer['q'] = np.zeros(samples_in_time_plots) # Q buffer (time domain)
    timeQ_line = time_plot.line(t, np.zeros(len(t)), color="red", line_width=1) # set x values but use dummy values for y
    
    # Waterfall Sink ("image" plot)
    waterfall_plot = pysdr.base_plot(' ', 'Time', 'Waterfall', disable_all_zooming=True) 
    waterfall_plot._set_x_range(0, fft_size) # Bokeh tries to automatically figure out range, but in this case we need to specify it
    waterfall_plot._set_y_range(0, waterfall_samples)
    waterfall_plot.axis.visible = False # i couldn't figure out how to update x axis when freq changes, so just hide them for now
    shared_buffer['waterfall'] = np.ones((waterfall_samples, fft_size))*-100.0 # waterfall buffer
    waterfall_data = waterfall_plot.image(image = [shared_buffer['waterfall']],  # input has to be in list form
                                          x = 0, # start of x
                                          y = 0, # start of y
                                          dw = fft_size, # size of x
                                          dh = waterfall_samples, # size of y
                                          palette = "Spectral9") # closest thing to matlab's jet    
    
    # IQ/Constellation Sink ("circle" plot)
    iq_plot = pysdr.base_plot(' ', ' ', 'IQ Plot')
    #iq_plot._set_x_range(-1.0, 1.0) # this is to keep it fixed at -1 to 1. you can also just zoom out with mouse wheel and it will stop auto-ranging
    #iq_plot._set_y_range(-1.0, 1.0)
    # we will use the same data buffers as the time-domain plot
    iq_data = iq_plot.circle(np.zeros(samples_in_time_plots), 
                             np.zeros(samples_in_time_plots),
                             line_alpha=0.0, # setting line_width=0 didn't make it go away, but this works
                             fill_color="aqua",
                             fill_alpha=0.5, 
                             size=4) # size of circles

    # Utilization bar (standard plot defined in gui.py)
    utilization_plot = pysdr.utilization_bar(1.0) # sets the top at 10% instead of 100% so we can see it move
    shared_buffer['utilization'] = 0.0 # float between 0 and 1, used to store how the process_samples is keeping up
    utilization_data = utilization_plot.quad(top=[shared_buffer['utilization']], bottom=[0], left=[0], right=[1], color="#B3DE69") #adds 1 rectangle
    
    def gain_callback(attr, old, new):
        gain = new # set new gain (leave it as a string)
        print "Setting gain to ", gain
        command = 'set_gain(' + gain + ')'
        shared_buffer['usrp-signal'] = (True, command)

    def freq_callback(attr, old, new):
        center_freq = float(new) # TextInput provides a string
        f = np.linspace(-samp_rate/2.0, samp_rate/2.0, fft_size) + center_freq
        fft_line.data_source.data['x'] = f/1e6 # update x axis of freq sink
        print "Setting freq to ", center_freq
        command = 'set_center_freq(' + str(center_freq) + ')'
        shared_buffer['usrp-signal'] = (True, command)        

    # gain selector
    gain_select = Select(title="Gain:", value=str(gain), options=[str(i*10) for i in range(8)])
    gain_select.on_change('value', gain_callback)
    
    # center_freq TextInput
    freq_input = TextInput(value=str(center_freq), title="Center Freq [Hz]")
    freq_input.on_change('value', freq_callback)
    
    # add the widgets to the document
    doc.add_root(row([widgetbox(gain_select, freq_input), utilization_plot])) # widgetbox() makes them a bit tighter grouped than column()

    # Add four plots to document, using the gridplot method of arranging them
    doc.add_root(gridplot([[fft_plot, time_plot], [waterfall_plot, iq_plot]], sizing_mode="scale_width", merge_tools=False)) # Spacer(width=20, sizing_mode="fixed")
    
    # This function gets called periodically, and is how the "real-time streaming mode" works   
    def plot_update():  
        timeI_line.data_source.data['y'] = shared_buffer['i'] # send most recent I to time sink
        timeQ_line.data_source.data['y'] = shared_buffer['q'] # send most recent Q to time sink
        iq_data.data_source.data = {'x': shared_buffer['i'], 'y': shared_buffer['q']} # send I and Q in one step using dict
        fft_line.data_source.data['y'] = shared_buffer['psd'] # send most recent psd to freq sink
        waterfall_data.data_source.data['image'] = [shared_buffer['waterfall']] # send waterfall 2d array to waterfall sink
        utilization_data.data_source.data['top'] = [shared_buffer['utilization']] # send most recent utilization level (only need to adjust top of rectangle)

    # Add a periodic callback to be run every x milliseconds
    doc.add_periodic_callback(plot_update, 150) 
    
    # pull out a theme from themes.py
    doc.theme = pysdr.black_and_white


# Assemble app
myapp = pysdr.pysdr_app() # start new pysdr app
myapp.set_bokeh_doc(main_doc) # provide Bokeh document defined above
myapp.create_bokeh_server()
myapp.create_web_server() 
myapp.start_web_server() # start web server.  blocking







