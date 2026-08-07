(() => {
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  if (reducedMotion || typeof window.gsap === 'undefined') {
    return;
  }

  const { gsap } = window;
  const landing = document.querySelector('.landing');
  const cursorHalo = document.querySelector('.cursor-halo');
  const stripes = gsap.utils.toArray('.stripe');

  /*
    Fine-pointer-only ambient light. The halo follows with a small amount of
    inertia so it feels attached to the cursor without looking like a hard
    flashlight. Because the element is below .stripes in the stacking order,
    dark bars never receive the light.
  */
  if (landing && cursorHalo && window.matchMedia('(hover: hover) and (pointer: fine)').matches) {
    gsap.set(cursorHalo, {
      xPercent: -50,
      yPercent: -50,
      opacity: 0
    });

    const moveHaloX = gsap.quickTo(cursorHalo, 'x', {
      duration: 0.42,
      ease: 'power3.out'
    });

    const moveHaloY = gsap.quickTo(cursorHalo, 'y', {
      duration: 0.42,
      ease: 'power3.out'
    });

    const placeHalo = (event) => {
      const rect = landing.getBoundingClientRect();
      moveHaloX(event.clientX - rect.left);
      moveHaloY(event.clientY - rect.top);
    };

    landing.addEventListener('pointerenter', (event) => {
      placeHalo(event);
      gsap.to(cursorHalo, {
        opacity: 1,
        duration: 0.42,
        ease: 'power2.out',
        overwrite: 'auto'
      });
    });

    landing.addEventListener('pointermove', placeHalo, { passive: true });

    landing.addEventListener('pointerleave', () => {
      gsap.to(cursorHalo, {
        opacity: 0,
        duration: 0.5,
        ease: 'power2.out',
        overwrite: 'auto'
      });
    });
  }

  /*
    Each bar starts as a hairline. It first grows vertically from the bottom,
    then unfolds horizontally to its designed width. Because the transform
    origin is the left edge, the expansion reads as an actual left-to-right
    unfolding rather than a centered scale.
  */
  stripes.forEach((stripe) => {
    const finalWidth = parseFloat(getComputedStyle(stripe).width) || 1;
    const foldedWidth = 1;

    gsap.set(stripe, {
      scaleY: 0,
      scaleX: Math.min(1, foldedWidth / finalWidth),
      transformOrigin: '0% 100%'
    });
  });

  gsap.set('.brand__row-inner, .brand__descriptor-inner, .contact__line-inner', {
    yPercent: 115,
    opacity: 0
  });

  const tl = gsap.timeline({
    defaults: {
      ease: 'power4.out'
    }
  });

  tl.to(stripes, {
      scaleY: 1,
      duration: 1.45,
      stagger: {
        each: 0.055,
        from: 'start'
      },
      ease: 'expo.inOut'
    }, 0.06)
    .to('.brand__row-inner', {
      yPercent: 0,
      opacity: 1,
      duration: 1.05,
      stagger: 0.085
    }, 0.39)
    .to('.brand__descriptor-inner', {
      yPercent: 0,
      opacity: 1,
      duration: 0.85
    }, 0.69)
    .to(stripes, {
      scaleX: 1,
      duration: 1.15,
      stagger: {
        each: 0.07,
        from: 'start'
      },
      ease: 'expo.inOut'
    }, 0.76)
    .to('.contact__line-inner', {
      yPercent: 0,
      opacity: 1,
      duration: 0.72,
      stagger: 0.055,
      ease: 'power3.out'
    }, 0.97);

  // Keep the final frame clean after the entrance animation.
  tl.set('.brand__row-inner, .brand__descriptor-inner, .contact__line-inner, .stripe', {
    clearProps: 'willChange'
  });

  /*
    Give the stripe field a restrained physical response on fine-pointer devices.
    This is intentionally proximity-based instead of using CSS :hover: some of
    the early bars are only 1–3px wide, which would otherwise make the effect
    flicker as their hit area changes while scaling.

    The nearest stripe can swell by ~12%; immediate neighbours receive only a
    trace of the movement. The bars remain anchored vertically and expand from
    their centre after the entrance has completed.
  */
  if (landing && window.matchMedia('(hover: hover) and (pointer: fine)').matches) {
    const swellRadius = 42;
    const maxSwell = 0.12;
    const stripeScaleSetters = stripes.map((stripe) => gsap.quickTo(stripe, 'scaleX', {
      duration: 0.32,
      ease: 'power3.out'
    }));

    const resetStripes = () => {
      stripeScaleSetters.forEach((setScale) => setScale(1));
    };

    // Wait until unfolding is effectively complete before allowing interaction,
    // so cursor movement can never fight the entrance timeline.
    let stripesInteractive = false;
    tl.call(() => {
      stripesInteractive = true;
      gsap.set(stripes, { transformOrigin: '50% 100%' });
    }, null, 1.95);

    landing.addEventListener('pointermove', (event) => {
      if (!stripesInteractive) return;

      stripes.forEach((stripe, index) => {
        const rect = stripe.getBoundingClientRect();

        // Ignore the stripe field vertically when the cursor is above/below it.
        // Desktop bars fill the viewport; on mobile this naturally limits the
        // effect to the lower stripe section.
        const verticalDistance = event.clientY < rect.top
          ? rect.top - event.clientY
          : event.clientY > rect.bottom
            ? event.clientY - rect.bottom
            : 0;

        if (verticalDistance > 12) {
          stripeScaleSetters[index](1);
          return;
        }

        const centerX = rect.left + rect.width / 2;
        const distance = Math.abs(event.clientX - centerX);
        const influence = Math.max(0, 1 - distance / swellRadius);

        // Smoothstep keeps the response almost imperceptible at the edge of
        // the radius and lets it gently build only when the cursor is close.
        const easedInfluence = influence * influence * (3 - 2 * influence);
        stripeScaleSetters[index](1 + maxSwell * easedInfluence);
      });
    }, { passive: true });

    landing.addEventListener('pointerleave', resetStripes);
  }
})();
