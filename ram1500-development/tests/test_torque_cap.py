"""Pure synthetic tests of 100 Hz torque-cap forensic extraction."""
from ramtools.torque_cap import analyze,find_runs
from ramtools.timeline import Index

NS=1_000_000_000

def r(s,t,**kw):
  return {"service":s,"t":int(t*NS),"segment":30,"valid":True,**kw}

def fixture():
  data=[]
  for n in range(50):
    t=n/100
    output=-1.0 if .10<=t<=.31 else -.2
    data.extend([
      r("controlsState",t,**{"torqueState.active":True,"torqueState.output":output,
        "torqueState.desiredLateralAccel":2.2,"torqueState.actualLateralAccel":1.7}),
      r("carControl",t,**{"actuators.torque":-1 if .10<=t<=.31 else -.2,"latActive":True}),
      r("carOutput",t,**{"actuatorsOutput.torqueOutputCan":-345 if .10<=t<=.31 else -70}),
      r("carState",t,**{"vEgo":21.,"steeringTorqueEps":-300.,
                           "steeringTorque":0.,"steeringPressed":False})
    ])
  return data

def test_full_rate_and_no_alert():
  rows=fixture()
  out=analyze(rows)
  assert out["runCountAll"]==1
  v=out["runs"][0]
  assert v["runSampleCount"]>=20
  assert v["pairedCapSamples"]>=20
  assert v["maxAbsAppliedCAN"]==345
  assert v["latActivePairedSamples"]>=20

def test_command_clipped():
  rows=fixture()
  for x in rows:
    if x["service"]=="carOutput" and .10<=x["t"]/NS<=.31:
      x["actuatorsOutput.torqueOutputCan"]=-200.
  v=analyze(rows)["runs"][0]
  assert v["limitedByCarControllerProxySamples"]>=20
  assert v["at95PercentOfCommandCeilingSamples"]==0

def test_stale_or_future_does_not_match():
  rows=[r("carControl",.5,**{"actuators.torque":-1.}),
        r("carOutput",.5,**{"actuatorsOutput.torqueOutputCan":-350.}),
        r("controlsState",.2,**{"torqueState.active":True,"torqueState.output":-1.}),
        r("controlsState",.21,**{"torqueState.active":True,"torqueState.output":-1.}),
        r("controlsState",.22,**{"torqueState.active":True,"torqueState.output":-1.}),
        r("controlsState",.23,**{"torqueState.active":True,"torqueState.output":-1.}),
        r("controlsState",.24,**{"torqueState.active":True,"torqueState.output":-1.}),
        r("controlsState",.25,**{"torqueState.active":True,"torqueState.output":-1.}),
        r("controlsState",.26,**{"torqueState.active":True,"torqueState.output":-1.}),
        r("controlsState",.27,**{"torqueState.active":True,"torqueState.output":-1.}),
        r("controlsState",.28,**{"torqueState.active":True,"torqueState.output":-1.}),
        r("controlsState",.29,**{"torqueState.active":True,"torqueState.output":-1.}),
        r("controlsState",.30,**{"torqueState.active":True,"torqueState.output":-1.}),
        r("controlsState",.31,**{"torqueState.active":True,"torqueState.output":-1.})]
  v=analyze(rows)["runs"][0]
  assert v["pairedCapSamples"]==0

def test_gap_splits_and_validation():
  rows=[r("controlsState",t,**{"torqueState.active":True,"torqueState.output":1.}) for t in
        (.1,.11,.12,.13,.14,.15,.16,.17,.18,.19,.5,.51,.52,.53,.54,.55,.56,.57,.58,.59)]
  assert len(find_runs(Index(rows),min_seconds=.07))==2
  try:analyze(rows,maximum_runs=0)
  except ValueError:pass
  else:raise AssertionError("did not reject zero maximum_runs")
