# ตัวจำลอง firmware บน PC — ต้องมีแค่ g++ (ภาพหน้าจอ: python3 + Pillow เพิ่ม). ดูหัวไฟล์ sim_main.cpp
CXX      ?= g++
CXXFLAGS ?= -std=c++11 -Wall -Wextra -Werror -g -fsanitize=address,undefined
# -include sim_config.h + -DPANEL_SIM: ใช้ config ของตัวจำลองเสมอ ไม่แตะ ../src/config.h จริง (มี token)
SIMFLAGS  = $(CXXFLAGS) -DPANEL_SIM -include sim_config.h -I stubs -I ../src
SRC       = sim_main.cpp sim_config.h $(wildcard stubs/*.h) $(wildcard ../src/*.h) ../src/main.cpp
LAYOUTS   = 1 2 3 6

.PHONY: test shots clean
test: build/test_logic build/sim $(LAYOUTS:%=build/sim_n%)
	build/test_logic
	build/sim watchdog
	build/sim ui build/ui.dl
	for ms in 40 60 100 150 300; do build/sim touch /dev/null $$ms || exit 1; done
	build/sim badtoken
	build/sim stall
	for n in $(LAYOUTS); do build/sim_n$$n layout build/layout$$n.dl || exit 1; done

shots: test
	for f in build/*.dl; do python3 render.py $$f build/shots || exit 1; done

build/test_logic: test_logic.cpp ../src/panel_logic.h
	@mkdir -p build
	$(CXX) $(CXXFLAGS) -I ../src $< -o $@

build/sim: $(SRC)
	@mkdir -p build
	$(CXX) $(SIMFLAGS) sim_main.cpp -o $@

build/sim_n%: $(SRC)
	@mkdir -p build
	$(CXX) $(SIMFLAGS) -DSIM_NLIGHTS=$* sim_main.cpp -o $@

clean:
	rm -rf build
