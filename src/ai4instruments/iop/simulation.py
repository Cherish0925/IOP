"""Small laboratory models: components for domain-independent programs, and a fiber-coupled laser bench."""
import math
import random
import time
from concurrent.futures import CancelledError
from .drivers import Driver
from .store import now


class Laboratory(Driver):
    def __init__(self,*args):
        super().__init__(*args)
        self.value=float(self.config.get('initial',0))

    def describe(self):
        kind=self.config['kind']
        number=lambda unit,minimum=0: {'type':'number','unit':unit,'minimum':minimum}
        models={
            'temperature':{'set_temperature':{'parameters':{'kelvin':number('K')}},'read_temperature':{'parameters':{}}},
            'balance':{'tare':{'parameters':{}},'read_mass':{'parameters':{}}},
            'pump':{'dispense':{'parameters':{'milliliters':number('mL')}}},
            'daq':{'acquire':{'parameters':{'count':{'type':'integer','minimum':1,'maximum':10000,'default':32},'rate_hz':{'type':'number','minimum':0.1,'default':1000}}}},
        }
        return {'name':self.id,'mode':'simulation','kind':kind,'model':'laboratory-example/1','operations':models[kind]}

    def invoke(self,operation,arguments,cancelled):
        if cancelled.wait(float(self.config.get('latency',0.02))):raise CancelledError()
        result={'time':now(),'source':'simulation'}
        if operation=='set_temperature':
            self.value=arguments['kelvin'];result.update(value=self.value,unit='K')
        elif operation=='read_temperature':result.update(value=self.value,unit='K')
        elif operation=='tare':self.value=0;result.update(value=0,unit='g')
        elif operation=='read_mass':result.update(value=self.value,unit='g')
        elif operation=='dispense':
            amount=arguments['milliliters'];target=self.registry[self.config['balance']]
            target.value+=amount*float(self.config.get('density_g_ml',1))
            result.update(value=amount,unit='mL')
        elif operation=='acquire':
            count=arguments['count'];rate=arguments['rate_hz']
            if cancelled.wait(count/rate):raise CancelledError()
            result.update(values=[math.sin(2*math.pi*i/count) for i in range(count)],unit='V',sample_rate_hz=rate)
        return result


class Optics(Driver):
    """A fiber-coupled laser bench whose readings follow simple physical models.

    After switch-on the laser diode approaches its set temperature and power with one time constant.
    The table's slow thermal drift walks the beam's best position; a disturbance shakes the table and
    displaces that position at once. Coupling into the fiber falls off as a Gaussian of the mirror's
    distance from it. The camera's dark current doubles every 6 K of sensor temperature, which rises
    after start, and its pixels saturate at full scale. Rates and time constants are in each config.
    """
    def __init__(self,*args):
        super().__init__(*args)
        c=self.config;self.start=time.monotonic();self.rng=random.Random(c.get('seed',1))
        warmup=float(c.get('warmup_s',300))
        self.on=bool(c.get('on',True));self.since=self.start-(10*warmup if c.get('warmed') else 0)
        self.power=float(c.get('power_mw',10));self.shutter=True
        self.x=self.y=0.0;self.zero=0.0;self.exposure=float(c.get('exposure_ms',2))
        self.shift=[0.0,0.0];self.knocks=[]

    def elapsed(self):
        return time.monotonic()-self.start

    # Laser
    def warmth(self):
        return math.exp(-(time.monotonic()-self.since)/float(self.config.get('warmup_s',300))) if self.on else 1.0

    def temperature(self):
        c=self.config;ambient=float(c.get('ambient_c',22))
        return float(c.get('set_c',25))+(ambient-float(c.get('set_c',25)))*self.warmth() if self.on else ambient

    def emitted(self):
        return self.power*(1-0.08*self.warmth()) if self.on and self.shutter else 0.0

    # Optical table
    def best(self):
        drift=float(self.config.get('drift_mrad_per_h',0.3))*self.elapsed()/3600
        return (float(self.config.get('x0_mrad',0.35))+drift+self.shift[0],
                float(self.config.get('y0_mrad',-0.25))+0.5*drift+self.shift[1])

    def vibration(self):
        quiet=float(self.config.get('quiet_s',5));now_=time.monotonic()
        return float(self.config.get('vibration_um_s',0.4))+sum(10*s*math.exp(-(now_-t)/quiet) for t,s in self.knocks)

    # Fiber output, read by the power meter and imaged by the camera
    def fiber(self):
        c=self.config;laser=self.registry[c['laser']];mirror=self.registry[c['mirror']];bx,by=self.registry[c['table']].best()
        miss=((mirror.x-bx)**2+(mirror.y-by)**2)/float(c.get('acceptance_mrad',0.25))**2
        return laser.emitted()*float(c.get('efficiency',0.85))*math.exp(-miss)

    def dark(self):
        c=self.config;rise=float(c.get('sensor_rise_c',8))*(1-math.exp(-self.elapsed()/float(c.get('sensor_s',1800))))
        sensor=float(c.get('sensor_c',20))+rise
        return sensor,100+self.exposure*float(c.get('dark_counts_per_ms',0.5))*2**((sensor-20)/6)

    def describe(self):
        kind=self.config['kind']
        number=lambda unit,lo,hi,text: {'type':'number','unit':unit,'minimum':lo,'maximum':hi,'description':text}
        flag=lambda text: {'type':'boolean','description':text}
        wait={'description':'Wait without changing anything','effect':'observe','parameters':{'seconds':number('s',0,3600,'Time to wait')}}
        models={
            'laser':{
                'output':{'description':'Switch the diode on or off; switching on starts its warm-up','parameters':{'on':flag('Emit light')}},
                'set_power':{'description':'Set the output power','parameters':{'milliwatts':number('mW',0,float(self.config.get('max_mw',20)),'Output power')}},
                'shutter':{'description':'Open or close the shutter after the laser','parameters':{'open':flag('Pass the beam')}},
                'read_power':{'description':'Read the internal monitor photodiode','effect':'observe','parameters':{}},
                'read_temperature':{'description':'Read the diode temperature','effect':'observe','parameters':{}},
                'wait':wait},
            'mirror':{**{'set_'+a:{'description':f'Tilt the steering mirror about its {a} axis','parameters':{a:number('mrad',-2,2,'Tilt')}} for a in 'xy'},
                'where':{'description':'Read both tilts','effect':'observe','parameters':{}}},
            'table':{
                'read_vibration':{'description':'Read the table accelerometer as RMS velocity','effect':'observe','parameters':{}},
                'read_temperature':{'description':'Read the table temperature','effect':'observe','parameters':{}},
                'disturb':{'description':'Simulation only: shake the table, as a knock or a closed door would, displacing the beam',
                           'parameters':{'severity':number('',0,1,'0 none, 1 a hard knock')}},
                'wait':wait},
            'power_meter':{
                'read_power':{'description':'Read the power leaving the fiber','effect':'observe','parameters':{}},
                'zero':{'description':'Take the present reading as zero. Block the beam first: a zero taken with light on offsets every later reading','parameters':{}}},
            'camera':{
                'set_exposure':{'description':'Set the exposure time','parameters':{'exposure':number('ms',0.01,100,'Exposure time')}},
                'exposure':{'description':'Read the exposure time','effect':'observe','parameters':{}},
                'acquire':{'description':'Image the fiber output: a line profile, its peak and the number of saturated pixels','effect':'observe','parameters':{}},
                'acquire_dark':{'description':'Read a frame with the sensor shuttered: its mean is the dark level at this exposure','effect':'observe','parameters':{}}},
        }
        return {'name':self.config.get('name',self.id),'mode':'simulation','kind':kind,'model':'fiber-bench/1','operations':models[kind]}

    def invoke(self,operation,arguments,cancelled):
        seconds=arguments.get('seconds',float(self.config.get('latency',0.005)))
        if cancelled.wait(seconds):raise CancelledError()
        c=self.config;kind=c['kind'];result={'time':now(),'source':'simulation'}
        noise=lambda scale: self.rng.gauss(0,scale)
        if operation=='wait':result.update(elapsed_s=seconds)
        elif operation=='output':
            if arguments['on'] and not self.on:self.since=time.monotonic()
            self.on=arguments['on'];result.update(on=self.on)
        elif operation=='set_power':self.power=arguments['milliwatts'];result.update(value=self.power,unit='mW')
        elif operation=='shutter':self.shutter=arguments['open'];result.update(open=self.shutter)
        elif operation=='read_power' and kind=='laser':result.update(value=self.emitted()*(1+noise(0.001)),unit='mW')
        elif operation=='read_temperature' and kind=='laser':result.update(value=self.temperature()+noise(0.005),unit='°C')
        elif operation in ('set_x','set_y'):axis=operation[-1];setattr(self,axis,arguments[axis]);result.update(value=arguments[axis],unit='mrad')
        elif operation=='where':result.update(x=self.x,y=self.y,unit='mrad')
        elif operation=='read_vibration':result.update(value=self.vibration()*(1+noise(0.05)),unit='µm/s')
        elif operation=='read_temperature':result.update(value=21+0.5*self.elapsed()/3600+noise(0.005),unit='°C')
        elif operation=='disturb':
            severity=arguments['severity'];self.knocks.append((time.monotonic(),severity))
            angle=self.rng.uniform(0,2*math.pi);self.shift[0]+=0.4*severity*math.cos(angle);self.shift[1]+=0.4*severity*math.sin(angle)
            result.update(severity=severity)
        elif operation=='read_power':
            raw=self.fiber()+float(c.get('offset_mw',0.003))
            result.update(value=raw-self.zero+noise(float(c.get('noise',0.002))*raw+1e-4),unit='mW')
        elif operation=='zero':self.zero=self.fiber()+float(c.get('offset_mw',0.003));result.update(value=self.zero,unit='mW')
        elif operation=='set_exposure':self.exposure=arguments['exposure'];result.update(value=self.exposure,unit='ms')
        elif operation=='exposure':result.update(value=self.exposure,unit='ms')
        elif operation=='acquire_dark':
            sensor,level=self.dark();result.update(mean=level+noise(0.3),sensor_c=sensor,unit='counts')
        elif operation=='acquire':
            pixels=int(c.get('pixels',64));full=int(c.get('full_scale',4095));_,level=self.dark()
            amplitude=float(c.get('counts_per_mw_ms',300))*self.registry[c['power_meter']].fiber()*self.exposure
            width=pixels/10;centre=(pixels-1)/2
            profile=[min(full,max(0,round(level+amplitude*math.exp(-((i-centre)/width)**2/2)+noise(3)))) for i in range(pixels)]
            result.update(peak=max(profile),saturated=profile.count(full),profile=profile,exposure_ms=self.exposure,unit='counts')
        return result
