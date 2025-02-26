import os
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
import openseespy.opensees as ops


# --------------------------
# 1. 定义自定义的 RL 环境
# --------------------------
class BuildingEnv10Floors:

    def __init__(self, accel_file="accel.txt", dt=0.02, code_drift=1 / 100.0):
        # 读取地震波(3000行)
        self.acc_data = np.loadtxt(accel_file)
        self.num_steps = len(self.acc_data)

        self.dt = dt
        self.floor_num = 10
        self.code_drift = code_drift  # 层间位移角限值(暂定1/100)
        self.episode_count = 0  # 开始时wipe + rebuild
        self.model_built = False

        # 状态空间与动作空间（示例：状态仅用上一次的 alphavalue；动作为 10 种可能楼层）
        # 状态: shape=(1,) => [alphavalue]
        # 动作: 10 个离散动作 => 选择楼层1..10
        self.state_dim = 1
        self.action_dim = self.floor_num

        # 初始化
        self.reset()

    def reset(self):

        self.last_alphavalue = 0.0
        # 将状态设为 [0.0]
        state = np.array([self.last_alphavalue], dtype=np.float32)
        return state

    def step(self, action):

        self.episode_count += 1
        if (self.episode_count % 10) == 1:
            # 每10轮重新wipe并建模，防止溢出
            self.build_model()

        # 运行分析
        damper_floor = action + 1  # 动作0 => 第1层, 动作1 => 第2层, ...
        alphavalue, max_disps = self.run_opensees_analysis(damper_floor)

        # 计算奖励
        reward = -alphavalue  # 越小越好

        # 记录最后的 alphavalue
        self.last_alphavalue = alphavalue
        next_state = np.array([alphavalue], dtype=np.float32)

        # 输出并保存每层最大位移
        self._save_floor_disps(max_disps)

        # 单步回合 => done=True
        done = True

        return next_state, reward, done, {}

    def build_model(self):
        #备份部分
        ops.wipe()
        self.model_built = True

        pass

    def run_opensees_analysis(self, damper_floor): #opensees主程序

        # 1) 清理
        ops.wipe()

        # 2) 建模
        ops.model('basic', '-ndm', 1, '-ndf', 1)

        # 创建节点 1~11
        for i in range(self.floor_num + 1):
            nodeTag = i + 1
            ops.node(nodeTag, float(i) * 3.0)
        # 固定底部
        ops.fix(1, 1)

        # 质量
        mass_per_floor = 1.0e5
        for nd in range(2, self.floor_num + 2):
            ops.mass(nd, mass_per_floor)

        # 弹簧材料
        matTag = 1
        k_floor = 1.0e8
        ops.uniaxialMaterial('Elastic', matTag, k_floor)

        # 相邻楼层链接
        elementTag = 1
        for i in range(1, self.floor_num):
            ops.element('twoNodeLink', elementTag, i + 1, i + 2,
                        '-mat', matTag, '-dir', 1)
            elementTag += 1

        # 在指定楼层添加阻尼器(假设 material 一样，也可以换另一个 matTag)
        if 1 <= damper_floor < self.floor_num:
            ops.element('twoNodeLink', elementTag, damper_floor, damper_floor + 1,
                        '-mat', matTag, '-dir', 1)
            elementTag += 1

        # 3) 加速度输入
        ops.timeSeries('Path', 1, '-dt', self.dt, '-values', *self.acc_data)
        ops.pattern('UniformExcitation', 1, 1, '-accel', 1)

        # 4) 阻尼
        zeta = 0.02
        alphaM = 0.0
        betaK = 0.0
        ops.rayleigh(alphaM, betaK, 0.0, 0.0)

        # 5) 分析选项
        ops.system('BandGeneral')
        ops.numberer('Plain')
        ops.constraints('Plain')
        ops.integrator('Newmark', 0.5, 0.25)
        ops.algorithm('Newton')
        ops.analysis('Transient')

        # 6) 时程分析 (3000 步)
        max_disps = np.zeros(self.floor_num)  # 仅记录楼层节点(2~11)
        for step in range(self.num_steps):
            ok = ops.analyze(1, self.dt)
            if ok != 0:
                break
            # 记录最大位移
            for nd in range(2, self.floor_num + 2):
                d = ops.nodeDisp(nd, 1)
                index = nd - 2  # 0~9
                if abs(d) > abs(max_disps[index]):
                    max_disps[index] = d

        # 7) 计算最大层间位移角
        max_story_drift = 0.0
        for i in range(self.floor_num - 1):
            drift = abs(max_disps[i + 1] - max_disps[i]) / 3.0  # 层高3.0
            if drift > max_story_drift:
                max_story_drift = drift

        # 8) 计算 alphavalue = |(max_story_drift) - (规范限值)|
        alphavalue = abs(max_story_drift - self.code_drift)

        return alphavalue, max_disps

    def _save_floor_disps(self, max_disps):
       #记录部分
        if not os.path.exists("floor_disp"):
            os.makedirs("floor_disp")
        # max_disps[0] => 楼层1, max_disps[1] => 楼层2, ...
        for i in range(self.floor_num):
            floor_index = i + 1
            filename = f"floor_disp/floor{floor_index}_disp.txt"
            with open(filename, "a") as f:
                f.write(f"{max_disps[i]:.6e}\n")


#神经网络部分
class PolicyNetwork(nn.Module):

    def __init__(self, state_dim, action_dim, hidden_size=64): #三层
        super(PolicyNetwork, self).__init__()
        self.net = nn.Sequential(
            nn.Linear(state_dim, hidden_size),
            nn.ReLU(),
            nn.Linear(hidden_size, hidden_size),
            nn.ReLU(),
            nn.Linear(hidden_size, action_dim),
        )

    def forward(self, x):
        # 返回每个动作的评分，然后需要 softmax
        logits = self.net(x)
        return logits

    def get_action_and_logp(self, state):

        x = torch.FloatTensor(state).unsqueeze(0)  # shape=(1, state_dim)
        logits = self.forward(x)  # shape=(1, action_dim)
        probs = torch.softmax(logits, dim=-1)  # shape=(1, action_dim)

        # 从这个分布中采样
        dist = torch.distributions.Categorical(probs=probs)
        action = dist.sample()  # shape=(1,)
        logp = dist.log_prob(action)  # shape=(1,)

        return action.item(), logp



def train_rl_model(num_episodes=200,
                   lr=1e-3,
                   gamma=0.99):
    # 1) 创建环境 & 策略网络 & 优化器
    env = BuildingEnv10Floors(accel_file="accel.txt")
    policy = PolicyNetwork(env.state_dim, env.action_dim, hidden_size=64)
    optimizer = optim.Adam(policy.parameters(), lr=lr)

    # 2) 开始训练
    for episode in range(num_episodes):
        # 重置环境
        state = env.reset()

        # 用策略网络采样动作
        action, logp = policy.get_action_and_logp(state)

        # 与环境交互
        next_state, reward, done, info = env.step(action)

        # 计算回报(因为是单步回合，这里回报就是 reward 本身)
        # 如果要多步，可以记录 trajectory，再计算梯度
        G = reward  # 单步直接是 reward

        # 计算损失 = - logp * G
        loss = -logp * G

        # 反向传播
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

        # 打印
        print(f"Episode [{episode + 1}/{num_episodes}], Action={action}, Reward={reward:.6f}, Loss={loss.item():.6f}")

    print("训练结束！")

# 主函数入口
if __name__ == "__main__":
    train_rl_model(num_episodes=200)
