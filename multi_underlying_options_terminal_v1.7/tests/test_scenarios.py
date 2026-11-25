from src.options_terminal.scenarios import future_iv, build_spot_grid

def test_absolute_iv_points(): assert abs(future_iv(.16,"Absolute points",2)-.18)<1e-9
def test_relative_iv(): assert abs(future_iv(.16,"Relative %",10)-.176)<1e-9
def test_grid(): assert list(build_spot_grid(-1,1,.5))==[-1,-.5,0,.5,1]
