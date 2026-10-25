import { useEffect, useRef, useState } from 'react'

function AuthRunner({ active = false }) {
  const trackRef = useRef(null)
  const dinoRef = useRef(null)
  const modeRef = useRef('auto')
  const gameOverRef = useRef(false)
  const jumpRequestRef = useRef(false)
  const manualStartRef = useRef(0)
  const scoreRef = useRef(0)
  const [runnerMode, setRunnerMode] = useState('auto')
  const [gameOver, setGameOver] = useState(false)
  const [runId, setRunId] = useState(0)
  const [score, setScore] = useState(0)

  useEffect(() => {
    modeRef.current = runnerMode
  }, [runnerMode])

  useEffect(() => {
    gameOverRef.current = gameOver
  }, [gameOver])

  useEffect(() => {
    const dinoNode = dinoRef.current
    const trackNode = trackRef.current

    if (!active || !dinoNode || !trackNode) {
      return undefined
    }

    let frame = 0
    let lastTime = performance.now()
    let jumpY = 0
    let jumpVelocity = 0
    let activeGravity = 820
    let jumping = false
    const dinoX = dinoNode.offsetLeft || 42
    const dinoWidth = 28
    const baseSpeed = 138
    const maxSpeed = 226
    const speedRamp = 1.45
    const autoGravity = 820
    const autoJumpPower = 255
    const manualGravity = 650
    const manualJumpPower = 278
    const startTime = performance.now()
    const moverNodes = Array.from(trackNode.querySelectorAll('[data-runner-kind]'))
    const trackWidth = trackNode.clientWidth || 450
    const movers = moverNodes
      .map((node, index) => ({
        node,
        kind: node.dataset.runnerKind,
        x: trackWidth + [36, 192, 372, 90, 268, 446, 625][index],
        width: node.dataset.runnerKind === 'cactus'
          ? (node.classList.contains('is-double') ? 35 : 16)
          : 8,
      }))

    const resetMover = (mover) => {
      const furthest = movers.reduce((max, item) => Math.max(max, item.x), 0)
      const patternGap = mover.kind === 'cactus'
        ? (mover.node.classList.contains('is-double') ? 190 : 150)
        : 120
      mover.x = Math.max(furthest + patternGap, trackWidth + 70 + Math.random() * 90)
    }

    const startJump = (currentSpeed, isManualJump = false) => {
      jumping = true
      activeGravity = isManualJump ? manualGravity : autoGravity
      jumpVelocity =
        (isManualJump ? manualJumpPower : autoJumpPower) +
        Math.max(0, currentSpeed - baseSpeed) * (isManualJump ? 0.12 : 0.16)
    }

    const hasCollision = () =>
      movers.some((mover) => {
        if (mover.kind !== 'cactus') {
          return false
        }
        const obstacleLeft = mover.x - 7
        const obstacleRight = mover.x + (mover.width || 16)
        const dinoLeft = dinoX + 3
        const dinoRight = dinoX + dinoWidth - 3
        return obstacleRight > dinoLeft && obstacleLeft < dinoRight && jumpY < 22
      })

    const step = (time) => {
      if (gameOverRef.current) {
        return
      }

      const dt = Math.min((time - lastTime) / 1000, 0.035)
      lastTime = time
      const elapsed = Math.max(0, (time - startTime) / 1000)
      const currentSpeed = Math.min(maxSpeed, baseSpeed + elapsed * speedRamp)

      if (modeRef.current === 'manual') {
        const nextScore = Math.max(0, Math.floor((time - manualStartRef.current) / 100))
        if (nextScore !== scoreRef.current) {
          scoreRef.current = nextScore
          setScore(nextScore)
        }
      }

      movers.forEach((mover) => {
        mover.x -= currentSpeed * dt
        if (mover.x < -58) {
          resetMover(mover)
        }
        mover.node.style.transform = `translateX(${mover.x}px)`
      })

      const nextCactus = movers
        .filter((mover) => mover.kind === 'cactus' && mover.x > dinoX - 18)
        .sort((a, b) => a.x - b.x)[0]

      if (modeRef.current === 'auto' && !jumping && nextCactus) {
        const cactusLeft = nextCactus.x - 7
        const gap = cactusLeft - (dinoX + dinoWidth)
        const speedBonus = Math.max(0, currentSpeed - baseSpeed) * 0.12
        const triggerGap = Math.min(42, 14 + (nextCactus.width || 16) * 0.42 + speedBonus)
        if (gap < triggerGap && gap > -24) {
          startJump(currentSpeed)
        }
      }

      if (modeRef.current === 'manual' && jumpRequestRef.current) {
        if (!jumping && jumpY <= 0) {
          startJump(currentSpeed, true)
        }
        jumpRequestRef.current = false
      }

      if (jumping) {
        jumpY += jumpVelocity * dt
        jumpVelocity -= activeGravity * dt
        if (jumpY <= 0) {
          jumpY = 0
          jumpVelocity = 0
          jumping = false
        }
      }

      dinoNode.classList.toggle('is-jumping', jumping)
      dinoNode.style.transform = `translateY(${-jumpY}px)`

      if (modeRef.current === 'manual' && hasCollision()) {
        gameOverRef.current = true
        dinoNode.classList.add('is-crashed')
        setGameOver(true)
        return
      }

      frame = requestAnimationFrame(step)
    }

    frame = requestAnimationFrame(step)

    return () => {
      cancelAnimationFrame(frame)
      dinoNode.classList.remove('is-jumping')
      dinoNode.classList.remove('is-crashed')
      dinoNode.style.transform = ''
      moverNodes.forEach((node) => {
        if (node) {
          node.style.transform = ''
        }
      })
    }
  }, [active, runId])

  const requestManualJump = () => {
    if (gameOver) {
      return
    }
    if (modeRef.current !== 'manual') {
      manualStartRef.current = performance.now()
      scoreRef.current = 0
      setScore(0)
    }
    modeRef.current = 'manual'
    setRunnerMode('manual')
    jumpRequestRef.current = true
    trackRef.current?.focus()
  }

  const restartManual = () => {
    gameOverRef.current = false
    jumpRequestRef.current = false
    manualStartRef.current = performance.now()
    scoreRef.current = 0
    modeRef.current = 'manual'
    setRunnerMode('manual')
    setGameOver(false)
    setScore(0)
    setRunId((value) => value + 1)
  }

  const resumeAuto = () => {
    gameOverRef.current = false
    jumpRequestRef.current = false
    manualStartRef.current = 0
    scoreRef.current = 0
    modeRef.current = 'auto'
    setRunnerMode('auto')
    setGameOver(false)
    setScore(0)
    setRunId((value) => value + 1)
  }

  return (
    <div className={`auth-runner ${active ? 'is-active' : ''}`}>
      <div
        aria-label="Мини-игра T-Rex"
        className={`auth-runner-track ${runnerMode === 'manual' ? 'is-manual' : 'is-auto'} ${gameOver ? 'is-game-over' : ''}`}
        onKeyDown={(event) => {
          if (event.key === ' ' || event.key === 'ArrowUp' || event.key === 'Enter') {
            event.preventDefault()
            requestManualJump()
          }
        }}
        onPointerDown={(event) => {
          if (event.target.closest('.auth-runner-game-button')) {
            return
          }
          requestManualJump()
        }}
        ref={trackRef}
        role="button"
        tabIndex={0}
      >
        <div className="auth-runner-scene">
          <span className="auth-runner-sky" />
          <span className="auth-runner-line" />
          <svg className="auth-runner-cloud cloud-one" viewBox="0 0 80 28" role="img">
            <path d="M8 22h55c8 0 13-4 13-10S70 2 63 2c-4 0-7 1-10 4C50 2 45 0 39 0c-8 0-15 5-18 12-2-2-5-3-8-3C6 9 1 14 1 20c0 1 0 2 1 2h6z" />
          </svg>
          <svg className="auth-runner-cloud cloud-two" viewBox="0 0 80 28" role="img">
            <path d="M8 22h55c8 0 13-4 13-10S70 2 63 2c-4 0-7 1-10 4C50 2 45 0 39 0c-8 0-15 5-18 12-2-2-5-3-8-3C6 9 1 14 1 20c0 1 0 2 1 2h6z" />
          </svg>
          <span className="auth-runner-bump bump-one" data-runner-kind="bump" />
          <span className="auth-runner-bump bump-two" data-runner-kind="bump" />
          <span className="auth-runner-bump bump-three" data-runner-kind="bump" />
          <span className="auth-runner-dino" ref={dinoRef}>
            <span className="auth-runner-dino-eye" />
            <span className="auth-runner-dino-arm" />
          </span>
          <span className="auth-runner-cactus cactus-one" data-runner-kind="cactus">
            <span className="auth-runner-mini-cactus" />
            <span className="auth-runner-mini-cactus is-extra" />
          </span>
          <span className="auth-runner-cactus cactus-two is-double" data-runner-kind="cactus">
            <span className="auth-runner-mini-cactus" />
            <span className="auth-runner-mini-cactus is-extra" />
          </span>
          <span className="auth-runner-cactus cactus-three" data-runner-kind="cactus">
            <span className="auth-runner-mini-cactus" />
            <span className="auth-runner-mini-cactus is-extra" />
          </span>
        </div>

        {runnerMode === 'manual' && (
          <div className="auth-runner-score" aria-label={`Очки ${score}`}>
            {String(score).padStart(5, '0')}
          </div>
        )}

        {gameOver && (
          <div className="auth-runner-game-over" onPointerDown={(event) => event.stopPropagation()}>
            <button className="auth-runner-game-button" onClick={restartManual} type="button">
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M4 12a8 8 0 1 0 2.34-5.66" />
                <path d="M4 4v6h6" />
              </svg>
              <span>Заново</span>
            </button>
            <button className="auth-runner-game-button" onClick={resumeAuto} type="button">
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M8 5v14l11-7z" />
              </svg>
              <span>Авто</span>
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

export default AuthRunner
